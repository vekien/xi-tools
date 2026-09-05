"""Explain any NPC's events, and survey an opcode across every zone.

``explain``: an annotated disassembly of one actor (or one event) with every operand
resolved — references[] values, registers named by role (Z[0] = menu choice, Z[1] = the
server's ``option``, Z[2+n] = server parameter n), dialog text inline, ``if`` conditions
spelled out, jump targets as labels, 0x9D tables expanded, and the unlisted subroutines
reached through 0x1A decoded once. Ends with a feature summary (menu, server round trip,
item window, number input, shop routine, ...) so a pattern can be recognised at a glance.

``survey``: every use of one opcode (optionally one sub-opcode) in every zone's event DAT,
with its operands resolved and the last dialog line printed before it — how the number
window parameters were pinned down.

Sizes come from :mod:`xi.event.xi_shop` (corrected 0xD4) on top of the core table.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from xi.dialog import xi_dialog
from xi.event import xi_event as core
from xi.event.xi_shop import opcode_size

REF_FLAG = 0x8000

IF_KINDS = {0: "== (else jump)", 1: "== -> jump", 2: "<= -> jump", 3: ">= -> jump",
            4: "< -> jump", 5: "> -> jump", 6: "!= -> jump", 9: "!= -> jump"}

# Operand layouts for annotation: (kind, byte offset) with kinds
#   s = work-selector u16, o = absolute scene offset u16, e = entity id u32, b = raw byte, t = 0x9D table u16
LAYOUT = {
    0x01: (("o", 1),), 0x02: (("s", 1), ("s", 3), ("b", 5), ("o", 6)), 0x03: (("s", 1), ("s", 3)),
    0x05: (("s", 1),), 0x06: (("s", 1),), 0x07: (("s", 1), ("s", 3)), 0x08: (("s", 1), ("s", 3)),
    0x09: (("s", 1), ("s", 3)), 0x0B: (("s", 1),), 0x0C: (("s", 1),), 0x10: (("s", 1), ("s", 3)),
    0x14: (("s", 1), ("s", 3)), 0x15: (("s", 1), ("s", 3)), 0x1A: (("o", 1),), 0x1C: (("s", 1),),
    0x1D: (("s", 1),), 0x1E: (("e", 1),), 0x20: (("b", 1),), 0x22: (("b", 1),),
    0x24: (("s", 1), ("s", 3), ("s", 5)), 0x2B: (("e", 1), ("s", 5)),
    0x3C: (("s", 1), ("s", 3), ("s", 5)), 0x3D: (("s", 1), ("s", 3), ("s", 5)), 0x3E: (("s", 1), ("s", 3), ("o", 5)),
    0x38: (("s", 1),), 0x40: (("s", 1), ("s", 3), ("s", 5), ("s", 7)), 0x41: (("s", 1), ("s", 3), ("s", 5), ("s", 7)),
    0x43: (("b", 1),), 0x46: (("b", 1),), 0x48: (("s", 1),), 0x49: (("s", 1), ("s", 3), ("s", 5)),
    0x4A: (("e", 1), ("e", 5)), 0x4E: (("b", 1), ("e", 2)), 0x83: (("s", 1),), 0x85: (("b", 1),),
    0x93: (("s", 1),), 0x9D: (("b", 1), ("t", 2), ("s", 4), ("s", 6)),
}
D4_LAYOUT = {0x00: (("s", 2), ("s", 4), ("s", 6)), 0x02: (("s", 2), ("s", 4), ("s", 6)), 0x03: (("s", 2), ("s", 4)),
             0x01: (("s", 2), ("s", 4), ("s", 6))}
# 0xB6 sub 0x0B: set the event entity's look — race, hair, head, body, hands, legs, feet, main, sub (9 selectors)
B6_LAYOUT = {0x0B: tuple(("s", 2 + 2 * i) for i in range(9))}
INPUT_LAYOUT = {0x10: (("s", 2),), 0x11: (("s", 2),), 0x12: (("s", 2), ("s", 4)), 0x13: (("s", 2),),
                0x30: (("s", 2), ("s", 4)), 0x31: (("s", 2),)}

TEXT_OPS = {0x1D, 0x48, 0x2B, 0x24, 0x49}

# PS2 handler names (XiEvents docs, `// PS2: XiEvent::CodeXXX`) used to label opcodes the decoder
# only knows as unk_XX.
PS2_NAMES = {
    0x02: 'CodeIF',
    0x1f: 'CodeMOVE',
    0x24: 'CodeQUERY',
    0x25: 'CodeQUERYWAIT',
    0x28: 'CodeREQSW',
    0x29: 'CodeREQEW',
    0x2c: 'CodeSCHEDULOR',
    0x2d: 'CodeMAPSCHEDULOR',
    0x31: 'CodeSMOVE',
    0x40: 'CodeSETBITWORK',
    0x41: 'CodeGETBITWORK',
    0x45: 'CodeLOADSCHEDULER',
    0x46: 'CodeDEFCAMERA',
    0x4a: 'CodeDTURA',
    0x50: 'CodeENDSCHEDULOR',
    0x51: 'CodeENDMAPSCHEDULOR',
    0x52: 'CodeENDLOADSCHEDULER_Main',
    0x53: 'CodeWAITSCHEDULOR',
    0x54: 'CodeWAITMAPSCHEDULOR',
    0x55: 'CodeWAITLOADSCHEDULER_Main',
    0x5a: 'CodeMOVE2',
    0x5b: 'CodeLOADEXTSCHEDULERMain',
    0x62: 'CodeLOADEVENTSCHEDULER',
    0x65: 'CodeGETDISTANCEAA',
    0x66: 'CodeLOADEXTSCHEDULERMain',
    0x6c: 'CodeTRANSPAR',
    0x6e: 'CodeEMOT',
    0x71: 'CodeOPENPASSWIN',
    0x72: 'CodeGETWEATER',
    0x73: 'CodeMAGICSCHEDULOR',
    0x75: 'CodeLOADROOM',
    0x7e: 'CodeCHOCOBO',
    0x7f: 'CodeQUERYWAIT2',
    0x80: 'CodeLOADWAIT',
    0x8b: 'CodeSETEVENTMARK',
    0x9f: 'CodeLOADEVENTSCHEDULER2',
    0xa0: 'CodeWAITLOADSCHEDULER_Main',
    0xa1: 'CodeENDLOADSCHEDULER_Main',
    0xa2: 'CodeWAITLOADSCHEDULER_Main',
    0xa3: 'CodeENDLOADSCHEDULER_Main',
}
# Plain names for opcodes the decoder only knows as unk_XX (XiEvents OpCodes/*.md, verified on the
# Tenshodo coffer 10099 and the Curio Vendor Moogle 9601, 2026-09-03).
PLAIN_NAMES = {
    0x0F: "xor", 0x10: "shl", 0x14: "mul", 0x15: "div", 0x3F: "mod",
    0x3C: "set_bit",        # 3C <dst> <bit> <limit>: dst |= 1 << (bit & 31) while (bit >> 5) < limit
    0x3D: "clear_bit",      # 3D <dst> <bit> <limit>
    0xCC: "item_window2",   # CC 01 <item> <a> <b> <c>: item window with three augment words
}
FEATURES = {
    0x24: "menu", 0x25: "menu", 0x43: "server round trip", 0x93: "item window", 0x9D: "data tables (shop/list)",
    0xD4: "query window", 0x71: "input window", 0x85: "moogle menu", 0xA8: "map", 0xB8: "map", 0xB4: "string window",
    0x34: "load zone", 0x35: "load zone", 0x45: "scene task (camera/fade/anim)", 0x5B: "gesture", 0x66: "gesture",
    0x5C: "music", 0x77: "time/weather", 0x67: "hide HUD", 0x40: "bit fields", 0x41: "bit fields", 0x2F: "cast effect",
    0x1A: "subroutine call", 0x20: "player lock", 0x46: "cinematic camera", 0xB6: "look change", 0x73: "cast effect",
}


_KI_NAMES: Optional[dict] = None
_ITEM_NAMES: Optional[dict] = None


def _server_dir() -> Optional[Path]:
    """LandSandBoat checkout: XI_SERVER_DIR in the environment or .env (key item enum)."""
    import os
    d = os.environ.get("XI_SERVER_DIR")
    if not d:
        env = Path(__file__).resolve().parents[3] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("XI_SERVER_DIR="):
                    d = line.split("=", 1)[1].strip().strip('"').strip("'")
    return Path(d) if d and Path(d).exists() else None


def key_item_names() -> dict:
    """id -> "Crimson Key" from LandSandBoat's scripts/enum/key_item.lua (empty if absent)."""
    global _KI_NAMES
    if _KI_NAMES is None:
        _KI_NAMES = {}
        d = _server_dir()
        f = d / "scripts" / "enum" / "key_item.lua" if d else None
        if f and f.exists():
            import re
            for m in re.finditer(r"^\s+([A-Z0-9_]+)\s*=\s*(\d+)\s*,", f.read_text(encoding="utf-8", errors="replace"), re.M):
                _KI_NAMES.setdefault(int(m.group(2)), m.group(1).replace("_", " ").title())
    return _KI_NAMES


def _item_cache_path() -> Path:
    import tempfile
    return Path(tempfile.gettempdir()) / "xi-tools-item-names.json"


def _load_item_tables() -> None:
    """Parse the client's item DATs ONCE for both the singular and the plural name tables, and
    keep the result in a JSON cache keyed by the DATs' sizes and mtimes (a cold parse is about
    a minute; every sweep worker used to pay it twice)."""
    global _ITEM_NAMES, _ITEM_PLURALS
    if _ITEM_NAMES is not None and _ITEM_PLURALS is not None:
        return
    names: dict = {}
    plurals: dict = {}
    try:
        import json
        from xi.ui.items.xi_items import ITEM_DATS
        from xi.ui.items.xi_parser import parse_dat
        from xi.xi_config import FFXI_DIR
        present = []
        for cat_name, base_id, item_type, en_rom, jp_rom in ITEM_DATS:
            fp = Path(FFXI_DIR) / Path(en_rom.replace("/", "\\"))
            if fp.exists():
                st = fp.stat()
                present.append((cat_name, base_id, item_type, en_rom, jp_rom, st.st_size, int(st.st_mtime)))
        key = [[p[3], p[5], p[6]] for p in present]
        cache = _item_cache_path()
        try:
            if cache.exists():
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("key") == key:
                    names = {int(k): v for k, v in data["names"].items()}
                    plurals = {int(k): v for k, v in data["plurals"].items()}
                    _ITEM_NAMES, _ITEM_PLURALS = names, plurals
                    return
        except Exception:  # noqa: BLE001 — a bad cache is rebuilt
            pass
        for cat_name, base_id, item_type, en_rom, jp_rom, _, _ in present:
            for it in parse_dat(FFXI_DIR, cat_name, base_id, item_type, en_rom, jp_rom):
                if it.name:
                    names.setdefault(it.id, it.name)
                if getattr(it, "plural", ""):
                    plurals.setdefault(it.id, it.plural)
        try:
            cache.write_text(json.dumps({"key": key, "names": names, "plurals": plurals}), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    except Exception:      # noqa: BLE001 — names are a convenience, never a failure
        pass
    _ITEM_NAMES, _ITEM_PLURALS = names, plurals


def item_names() -> dict:
    """id -> item name from the client's item DATs (lazy; cached on disk, see _load_item_tables)."""
    _load_item_tables()
    return _ITEM_NAMES


_ITEM_PLURALS = None


def item_plurals() -> dict:
    """id -> plural item name from the client's item DATs (the log-name form)."""
    _load_item_tables()
    return _ITEM_PLURALS


def reg_name(v: int) -> str:
    if v & REF_FLAG:
        return f"ref[{v & 0x7FFF:#x}]"
    if 0x1700 <= v < 0x1800:
        return f"Z7[{v - 0x1700}]"
    if 0x1100 <= v < 0x1200:
        return f"ZM[{v - 0x1100}]"
    if 0x1000 <= v < 0x1100:
        i = v - 0x1000
        if i == 0:
            return "Z[0]=choice"
        if i == 1:
            return "Z[1]=option"
        if 2 <= i < 10:
            return f"Z[{i}]=param{i - 2}"
        return f"Z[{i}]"
    return f"L[{v}]"


@dataclass
class Listing:
    lines: list[str] = field(default_factory=list)
    features: set = field(default_factory=set)
    dialog_ids: list[int] = field(default_factory=list)
    calls: list[int] = field(default_factory=list)


class ActorExplainer:
    def __init__(self, scene: bytes, refs: list[int], dialog_blobs: Optional[list[bytes]],
                 names: Optional[dict[int, str]] = None):
        self.scene = scene
        self.refs = refs
        self.blobs = dialog_blobs
        self.names = names or {}

    # --- helpers ------------------------------------------------------------------
    def ref_value(self, sel: int) -> Optional[int]:
        if sel & REF_FLAG and (sel & 0x7FFF) < len(self.refs):
            return self.refs[sel & 0x7FFF]
        return None

    def sel(self, v: int) -> str:
        rv = self.ref_value(v)
        return f"{rv}" if rv is not None else reg_name(v)

    def text(self, msg_id: Optional[int], width: int = 96) -> str:
        if msg_id is None or not self.blobs or msg_id >= len(self.blobs):
            return ""
        try:
            t, _ = xi_dialog.decode_event_string(bytes(self.blobs[msg_id]))
        except Exception:
            return "<undecodable>"
        t = t.replace("\n", "\\n")
        return t if len(t) <= width else t[:width - 1] + "..."

    # --- key item / item names for constants the strings display by kind --------------
    @property
    def name_kinds(self) -> set:
        """Name-code kinds (``01 05 KIND 82``) used by any message this actor references."""
        if not hasattr(self, "_name_kinds"):
            kinds = set()
            if self.blobs:
                for rv in set(self.refs):
                    if 0 <= rv < len(self.blobs):
                        b = bytes(self.blobs[rv])
                        i = b.find(b"\x01\x05")
                        while i >= 0 and i + 3 < len(b):
                            if b[i + 3] == 0x82:
                                kinds.add(b[i + 2])
                            i = b.find(b"\x01\x05", i + 1)
            self._name_kinds = kinds
        return self._name_kinds

    def named(self, value: int) -> str:
        """`` (KI Crimson Key)`` / `` (item Gold Obi)`` for a constant, when the actor's strings
        show names of that kind; empty otherwise."""
        if 0x36 in self.name_kinds:
            n = key_item_names().get(value)
            if n:
                return f" (KI {n})"
        if self.name_kinds & {0x23, 0x24, 0x25}:
            n = item_names().get(value)
            if n:
                return f" (item {n})"
        return ""

    def entity(self, v: int) -> str:
        magic = {0x7FFFFFF0: "player", 0x7FFFFFF8: "event entity", 0x7FFFFFFF: "zone"}
        if v in magic:
            return magic[v]
        n = self.names.get(v)
        return f"0x{v:08X}" + (f" {n}" if n else "")

    def table(self, off: int, limit: int = 6) -> str:
        vals = []
        pos = off
        while pos + 2 <= len(self.scene) and len(vals) < 200:
            v = struct.unpack_from("<H", self.scene, pos)[0]
            rv = self.ref_value(v)
            if rv is None:
                vals.append(reg_name(v))
                if v == 0:
                    break
            else:
                if rv == 0:
                    break
                vals.append(str(rv))
            pos += 2
        vals = [v + self.named(int(v)) if v.isdigit() else v for v in vals]
        shown = ", ".join(vals[:limit]) + (f", ... ({len(vals)} entries)" if len(vals) > limit else "")
        return f"[{shown}]"

    # --- one opcode ---------------------------------------------------------------
    def annotate(self, pos: int) -> tuple[int, str, Optional[int], Optional[int]]:
        """Return (size, text, jump_target, call_target) for the opcode at ``pos``."""
        op = self.scene[pos]
        sub = self.scene[pos + 1] if pos + 1 < len(self.scene) else 0
        sz = opcode_size(op, sub)
        name = core._opcode_name(op)
        if name.startswith("unk_") and op in PLAIN_NAMES:
            name = PLAIN_NAMES[op]
        elif name.startswith("unk_") and op in PS2_NAMES:
            name = PS2_NAMES[op]
        if not sz:
            return 0, f"?? {op:02x} sub {sub:02x} (unknown size: listing stops here; see XiEvents OpCodes/0x{op:04X}.md)", None, None
        args = self.scene[pos + 1:pos + sz]
        jump = call = None
        notes: list[str] = []
        if op == 0xD4:
            layout = D4_LAYOUT.get(sub, ())
            notes.append(f"sub {sub:#04x}" + {0: " open map+query", 1: " set rows", 2: " open query window", 3: " set row item"}.get(sub, ""))
        elif op == 0xB6:
            layout = B6_LAYOUT.get(sub, ())
            notes.append(f"sub {sub:#04x}" + (" set look (race, hair, head, body, hands, legs, feet, main, sub)" if sub == 0x0B else ""))
        elif op == 0xCC:
            layout = (("s", 2), ("s", 4), ("s", 6), ("s", 8)) if sub in (0x00, 0x01, 0x03) else ()
            notes.append(f"sub {sub:#04x}" + (" item + augment words" if sub in (0x00, 0x01, 0x03) else ""))
        elif op == 0x71:
            layout = INPUT_LAYOUT.get(sub, ())
            notes.append(f"sub {sub:#04x} " + {0x00: "text window open", 0x01: "text window wait", 0x10: "number window open",
                                                 0x11: "number wait->", 0x12: "number window open (sized)", 0x13: "number wait->",
                                                 0x30: "number window open (quill)", 0x31: "number wait->", 0x40: "linkshell concierge"}.get(sub, "?"))
        else:
            layout = LAYOUT.get(op, ())
        for kind, o in layout:
            if o >= sz:
                continue
            if kind == "s":
                v = struct.unpack_from("<H", self.scene, pos + o)[0]
                notes.append(self.sel(v))
            elif kind == "o":
                t = struct.unpack_from("<H", self.scene, pos + o)[0]
                notes.append(f"-> @{t:04x}")
                if op == 0x1A:
                    call = t
                else:
                    jump = t
            elif kind == "e":
                v = struct.unpack_from("<I", self.scene, pos + o)[0]
                notes.append(self.entity(v))
            elif kind == "t":
                t = struct.unpack_from("<H", self.scene, pos + o)[0]
                notes.append(f"table@{t:04x} {self.table(t)}")
            elif kind == "b":
                notes.append(f"{self.scene[pos + o]}")
        if op == 0x03 and len(notes) >= 2:
            dst = struct.unpack_from("<H", self.scene, pos + 1)[0]
            src = struct.unpack_from("<H", self.scene, pos + 3)[0]
            rv = self.ref_value(src)
            if rv and (0x1002 <= dst <= 0x10FF or 0x1700 <= dst <= 0x17FF):
                notes[1] += self.named(rv)
        if op == 0x02 and sz == 8:
            kind = self.scene[pos + 5] & 0x0F
            a, b, t = notes[0], notes[1], notes[3]
            notes = [f"if {a} {IF_KINDS.get(kind, f'kind {kind}')} {b} {t}"]
        if op in TEXT_OPS:
            o = 5 if op == 0x2B else 1
            v = struct.unpack_from("<H", self.scene, pos + o)[0]
            mid = self.ref_value(v)
            if mid is not None:
                notes.append(f'"{self.text(mid)}"')
        if op == 0xD4 and sub in (0x00, 0x02):
            v = struct.unpack_from("<H", self.scene, pos + 2)[0]
            mid = self.ref_value(v)
            if mid is not None:
                notes.append(f'"{self.text(mid)}"')
        if op == 0x43:
            notes = ["send 0x05B (Z[1] -> server option)" if args[0] == 0 else "wait for the server's reply"]
        if op == 0x93:
            notes.append("(0 = close)" if notes and notes[0] == "0" else "item window")
        return sz, f"{name:14s} {' '.join(notes)}", jump, call

    # --- one region ---------------------------------------------------------------
    def region(self, start: int, stop: int, listing: Listing, stop_at_return: bool = False) -> int:
        """Annotate [start, stop); returns where the walk actually stopped."""
        pos = start
        labels = set()
        while pos < stop:
            op = self.scene[pos]
            sz, txt, jump, call = self.annotate(pos)
            if not sz:
                listing.lines.append(f"    +{pos:04x}  {txt}")
                break
            if jump is not None:
                labels.add(jump)
            if call is not None:
                listing.calls.append(call)
            listing.features.update({FEATURES[op]} if op in FEATURES else set())
            if op == 0x24 or op == 0xD4:
                listing.features.add("menu")
            if op != 0x00:
                listing.lines.append(f"    +{pos:04x}  {txt}")
            pos += sz
            if op == 0x21 or (stop_at_return and op == 0x1B):
                break
        return pos


def explain_actor(scene: bytes, refs: list[int], event_ids: list[int], event_offsets: list[int],
                  blobs: Optional[list[bytes]], names: Optional[dict[int, str]] = None,
                  only_event: Optional[int] = None) -> Listing:
    ex = ActorExplainer(scene, refs, blobs, names)
    out = Listing()
    pairs = sorted(zip(event_offsets, event_ids))
    bounds = [o for o, _ in pairs] + [len(scene)]
    seen_calls: set[int] = set()
    pending_calls: list[int] = []
    for i, (off, eid) in enumerate(pairs):
        if eid in (0xFFFE, 0xFFFF):
            continue
        if only_event is not None and eid != only_event:
            continue
        stop = bounds[i + 1]
        sub = Listing()
        end = ex.region(off, stop, sub)
        if not sub.lines and stop - off <= 1:
            out.lines.append(f"  Event {eid} @{off:04x}  (1-byte marker: this actor takes part in event {eid} of another actor)")
            continue
        out.lines.append(f"  Event {eid} @{off:04x}..{end:04x}")
        out.lines.extend(sub.lines)
        out.features |= sub.features
        pending_calls.extend(sub.calls)
    # subroutines reached through 0x1A (unlisted code between events)
    while pending_calls:
        t = pending_calls.pop(0)
        if t in seen_calls or t >= len(scene):
            continue
        seen_calls.add(t)
        nxt = min([b for b in bounds if b > t] + [len(scene)])
        sub = Listing()
        end = ex.region(t, nxt, sub, stop_at_return=True)
        out.lines.append(f"  Subroutine @{t:04x}..{end:04x} (called with 0x1A)")
        out.lines.extend(sub.lines)
        out.features |= sub.features
        pending_calls.extend(c for c in sub.calls if c not in seen_calls)
    out.features.discard("subroutine call") if not seen_calls else None
    return out


# ---------------------------------------------------------------------------
# Zone files
# ---------------------------------------------------------------------------

@dataclass
class ZoneFiles:
    zone_id: int
    name: str
    event: Optional[Path]
    dialog: Optional[Path]
    npc: Optional[Path]


def zone_files(ffxi_dir: Path, zone_ids: Optional[list[int]] = None, tables=None) -> list[ZoneFiles]:
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.zone.xi_inject import zone_event_file_id, zone_dialog_file_id, zone_npc_file_id
    from xi.zone.xi_list import get_zone_entries
    tables = tables or load_all_tables()
    zones = get_zone_entries(path_prefix="")
    out = []
    for z in zones:
        zid = z["id"]
        if zone_ids is not None and zid not in zone_ids:
            continue
        paths = {}
        for role, fn in (("event", zone_event_file_id), ("dialog", zone_dialog_file_id), ("npc", zone_npc_file_id)):
            try:
                hits = scan_file_ids([fn(zid)], tables)
            except Exception:
                hits = []
            p = ffxi_dir / hits[0]["dat"] if hits else None
            paths[role] = p if (p and p.is_file()) else None
        if paths["event"]:
            out.append(ZoneFiles(zid, z["name"], paths["event"], paths["dialog"], paths["npc"]))
    return out


def load_zone(zf: ZoneFiles):
    """(actors, dialog_blobs or None, names)"""
    actors = core.parse_raw_actors(zf.event.read_bytes())
    blobs = None
    if zf.dialog:
        try:
            blobs, _ = xi_dialog.raw_entry_blobs(zf.dialog.read_bytes())
        except Exception:
            blobs = None
    names = core.parse_entity_names(zf.npc.read_bytes()) if zf.npc else {}
    return actors, blobs, names


# ---------------------------------------------------------------------------
# Survey
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    zone_id: int
    zone: str
    actor_id: int
    actor: str
    event_id: Optional[int]
    offset: int
    operands: str
    previous_text: str
    next_op: str


def survey_zone(zf: ZoneFiles, op: int, sub: Optional[int]) -> Iterator[Hit]:
    actors, blobs, names = load_zone(zf)
    for a in actors:
        scene = bytes(a.scene_data)
        ex = ActorExplainer(scene, list(a.references), blobs, names)
        pairs = sorted(zip(a.event_offsets, a.event_ids))
        bounds = [o for o, _ in pairs] + [len(scene)]
        # walk every event region and then the gaps (subroutines) in one pass over the scene
        regions = [(o, bounds[i + 1], eid) for i, (o, eid) in enumerate(pairs)]
        seen_calls: set[int] = set()
        while regions:
            start, stop, eid = regions.pop(0)
            pos = start
            last_text = ""
            while pos < stop:
                o_ = scene[pos]
                s_ = scene[pos + 1] if pos + 1 < len(scene) else 0
                sz = opcode_size(o_, s_)
                if not sz:
                    break
                if o_ == 0x1A and pos + 3 <= len(scene):
                    t = struct.unpack_from("<H", scene, pos + 1)[0]
                    if t not in seen_calls and t < len(scene):
                        seen_calls.add(t)
                        regions.append((t, len(scene), eid))     # subroutine, attributed to the caller
                if o_ in TEXT_OPS:
                    v = struct.unpack_from("<H", scene, pos + (5 if o_ == 0x2B else 1))[0]
                    mid = ex.ref_value(v)
                    if mid is not None:
                        last_text = ex.text(mid, 120)
                if o_ == op and (sub is None or s_ == sub):
                    _, txt, _, _ = ex.annotate(pos)
                    npos = pos + sz
                    nxt = ex.annotate(npos)[1] if npos < len(scene) else ""
                    yield Hit(zf.zone_id, zf.name, a.actor_id, names.get(a.actor_id, ""), eid, pos,
                              txt, last_text, nxt)
                pos += sz
                if o_ == 0x21 or (o_ == 0x1B and start not in a.event_offsets):
                    break                                         # event end / subroutine return


# ---------------------------------------------------------------------------
# Zone entity-name DAT (file 6720 + zone): flat 32-byte records, name[28] cp932 NUL-padded
# + serverId u32, sorted by id, record 0 = "none"/0. The client shows an NPC's name only
# when its id is listed here (otherwise the spawn gets a generic name), so a new NPC needs
# one record plus its server row (LSB data/zones/<zone>/npcs.yaml).
# ---------------------------------------------------------------------------

RECORD = 32


def entity_records(data: bytes) -> list[tuple[int, str]]:
    out = []
    for off in range(0, (len(data) // RECORD) * RECORD, RECORD):
        sid = struct.unpack_from("<I", data, off + 28)[0]
        name = data[off:off + 28].split(b"\x00", 1)[0].decode("cp932", errors="replace")
        out.append((sid, name))
    return out


def next_free_entity_id(data: bytes, zone_id: int, gap: int = 1) -> int:
    ids = [sid for sid, _ in entity_records(data) if sid]
    base = 0x01000000 | (zone_id << 12)
    top = max(ids) if ids else base
    return top + gap


def add_entity_name(data: bytes, sid: int, name: str, replace: bool = False) -> bytes:
    """Insert (or replace) the record for ``sid`` keeping the table sorted by id."""
    raw = name.encode("cp932")
    if len(raw) > 27:
        raise ValueError("entity name must be at most 27 bytes (cp932)")
    rec = raw.ljust(28, b"\x00") + struct.pack("<I", sid)
    recs = [data[o:o + RECORD] for o in range(0, (len(data) // RECORD) * RECORD, RECORD)]
    out = []
    placed = False
    for r in recs:
        rid = struct.unpack_from("<I", r, 28)[0]
        if rid == sid:
            if not replace:
                raise ValueError(f"id 0x{sid:08X} already listed as {r[:28].split(b'\x00',1)[0].decode('cp932','replace')!r}")
            out.append(rec); placed = True; continue
        if not placed and rid > sid and rid != 0:
            out.append(rec); placed = True
        out.append(r)
    if not placed:
        out.append(rec)
    return b"".join(out)

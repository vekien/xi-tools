"""Pre-flight checks for a compiled (or retail) event before it is written to the client.

Walks one event of one actor with the corrected opcode sizes and reports:
  errors   — things the client will trip on: unknown opcode sizes, jumps that leave the
             event, calls outside the scene, selectors past references[], message ids past
             the dialog table, undecodable strings, menu strings without the 0x0B
             "option rows start" marker, no `end` before the next event starts
  warnings — suspicious but survivable: a menu with a single option row, a `43 00` not
             followed by `43 01`, an `if` with an unknown kind, a call target that never
             returns (no 0x1B before the next event/scene end)

The same walker serves ``xi event lint`` and the cutscene compiler's pre-flight check.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Optional

from xi.dialog import xi_dialog
from xi.event import xi_event as core
from xi.event.xi_explain import LAYOUT, D4_LAYOUT, INPUT_LAYOUT
from xi.event.xi_shop import opcode_size

REF_FLAG = 0x8000
OPTION_MARK = 0x0B


MAX_QUESTION_CHARS = 45   # longest plain retail menu question (Ru'Lude, Bastok Markets, Aht Urhgan, San d'Oria, Windurst)
MAX_ROW_CHARS = 48        # longest plain retail option row


def rendered_length(line: str) -> int:
    """Characters the client draws for one menu line: ``{n}`` placeholders count as three
    (a short number; names run longer, so leave headroom), ``{n}[a/b/c]`` selectors as
    their longest alternative."""
    line = re.sub(r"\{[^}]*\}\[([^\]]*)\]", lambda m: max(m.group(1).split("/"), key=len), line)
    line = re.sub(r"\{[^}]*\}", "123", line)
    return len(line.strip())


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    opcodes: int = 0
    calls: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _layout(op: int, sub: int):
    if op == 0xD4:
        return D4_LAYOUT.get(sub, ())
    if op == 0x71:
        return INPUT_LAYOUT.get(sub, ())
    return LAYOUT.get(op, ())


def lint_event(scene: bytes, refs: list[int], start: int, stop: int,
               blobs: Optional[list[bytes]] = None, follow_calls: bool = True) -> LintResult:
    """Lint the event whose code begins at ``start``; ``stop`` is the next event's offset
    (or the scene length). Data trailers after `end` are not walked."""
    r = LintResult()
    seen_calls: set[int] = set()

    def ref_value(sel: int) -> Optional[int]:
        if sel & REF_FLAG:
            idx = sel & 0x7FFF
            if idx >= len(refs):
                return None
            return refs[idx]
        return None

    def check_message(pos: int, sel: int, is_menu: bool) -> None:
        if not (sel & REF_FLAG):
            return                                    # register-held id (say_indexed): fine
        mid = ref_value(sel)
        if mid is None:
            r.errors.append(f"+{pos:04x}: message selector 0x{sel:04x} outside references[]")
            return
        if blobs is None:
            return
        if mid >= len(blobs):
            r.errors.append(f"+{pos:04x}: message id {mid} past the dialog table ({len(blobs)} entries)")
            return
        blob = bytes(blobs[mid])
        try:
            xi_dialog.decode_event_string(blob)
        except Exception as e:                        # noqa: BLE001
            r.errors.append(f"+{pos:04x}: message {mid} does not decode ({e})")
            return
        if is_menu:
            if OPTION_MARK not in blob:
                r.errors.append(f"+{pos:04x}: menu string {mid} has no 0x0B option-start marker "
                                f"(client would show every row as a comment)")
            elif blob.count(b"\x07", blob.index(OPTION_MARK)) < 1:
                r.warnings.append(f"+{pos:04x}: menu string {mid} has a single option row")
            else:
                # The menu box does not wrap: retail's longest plain question is 45 characters
                # and its longest plain row 48 (measured over five town dialog files).
                text, _ = xi_dialog.decode_event_string(blob)
                lines = [rendered_length(x) for x in text.replace("\\n", "\n").split("\n")]
                if lines and lines[0] > MAX_QUESTION_CHARS:
                    r.warnings.append(f"+{pos:04x}: menu string {mid} question is {lines[0]} characters; "
                                      f"retail stays within {MAX_QUESTION_CHARS} (the box does not wrap)")
                for k, ln in enumerate(lines[1:], 1):
                    if ln > MAX_ROW_CHARS:
                        r.warnings.append(f"+{pos:04x}: menu string {mid} row {k} is {ln} characters; "
                                          f"retail stays within {MAX_ROW_CHARS}")

    def walk(begin: int, limit: int, is_call: bool) -> None:
        pos = begin
        ended = False
        last = None
        while pos < limit:
            op = scene[pos]
            sub = scene[pos + 1] if pos + 1 < len(scene) else 0
            sz = opcode_size(op, sub)
            if not sz:
                r.errors.append(f"+{pos:04x}: opcode 0x{op:02x} (sub 0x{sub:02x}) has no known size; walk stopped")
                return
            if pos + sz > len(scene):
                r.errors.append(f"+{pos:04x}: opcode 0x{op:02x} runs past the scene end")
                return
            r.opcodes += 1
            for kind, o in _layout(op, sub):
                if o >= sz:
                    continue
                if kind == "s":
                    v = struct.unpack_from("<H", scene, pos + o)[0]
                    if v & REF_FLAG and (v & 0x7FFF) >= len(refs):
                        r.errors.append(f"+{pos:04x}: opcode 0x{op:02x} selector 0x{v:04x} outside references[] ({len(refs)})")
                elif kind == "o":
                    t = struct.unpack_from("<H", scene, pos + o)[0]
                    if op == 0x1A:
                        if t >= len(scene):
                            r.errors.append(f"+{pos:04x}: call to @{t:04x} outside the scene")
                        else:
                            r.calls.append(t)
                    elif not (start <= t < stop) and not (is_call and begin <= t < limit):
                        r.errors.append(f"+{pos:04x}: jump to @{t:04x} leaves the event (@{start:04x}..@{stop:04x})")
                elif kind == "t":
                    t = struct.unpack_from("<H", scene, pos + o)[0]
                    if t >= len(scene):
                        r.errors.append(f"+{pos:04x}: 0x9D table @{t:04x} outside the scene")
            if op in (0x1D, 0x48):
                check_message(pos, struct.unpack_from("<H", scene, pos + 1)[0], False)
            elif op == 0x2B:
                check_message(pos, struct.unpack_from("<H", scene, pos + 5)[0], False)
            elif op == 0x24:
                check_message(pos, struct.unpack_from("<H", scene, pos + 1)[0], True)
            elif op == 0xD4 and sub in (0x00, 0x02):
                check_message(pos, struct.unpack_from("<H", scene, pos + 2)[0], True)
            if op == 0x02:
                kind_b = scene[pos + 5] & 0x0F
                if kind_b not in (0, 1, 2, 3, 4, 5, 6, 9):
                    r.warnings.append(f"+{pos:04x}: if with unusual kind {kind_b}")
            if last is not None and last[0] == 0x43 and last[1] == 0 and not (op == 0x43 and sub == 1):
                r.warnings.append(f"+{last[2]:04x}: 43 00 (send) not followed by 43 01 (wait for the reply)")
            last = (op, sub, pos)
            pos += sz
            if op == 0x21:
                ended = True
                break
            if is_call and op == 0x1B:
                ended = True
                break
        if not ended:
            if is_call:
                r.warnings.append(f"call target @{begin:04x}: no 0x1B return before @{limit:04x}")
            else:
                r.errors.append(f"event @{start:04x}: no `end` (0x21) before the next event at @{stop:04x}")

    walk(start, stop, False)
    if follow_calls:
        pending = list(r.calls)
        while pending:
            t = pending.pop(0)
            if t in seen_calls:
                continue
            seen_calls.add(t)
            before = len(r.calls)
            walk(t, len(scene), True)
            pending.extend(c for c in r.calls[before:] if c not in seen_calls)
    return r


def lint_actor(actor, blobs: Optional[list[bytes]] = None, only_event: Optional[int] = None) -> dict[int, LintResult]:
    """Lint every real event of a RawActor. Returns {event_id: LintResult}."""
    scene = bytes(actor.scene_data)
    refs = list(actor.references)
    pairs = sorted(zip(actor.event_offsets, actor.event_ids))
    bounds = [o for o, _ in pairs] + [len(scene)]
    out = {}
    for i, (off, eid) in enumerate(pairs):
        if eid in (0xFFFE, 0xFFFF) or (only_event is not None and eid != only_event):
            continue
        if bounds[i + 1] - off <= 1:
            continue                                   # participation marker
        out[eid] = lint_event(scene, refs, off, bounds[i + 1], blobs)
    return out


def lint_dat(event_dat: bytes, dialog_dat: Optional[bytes], actor_id: int,
             only_event: Optional[int] = None) -> dict[int, LintResult]:
    actor = next((a for a in core.parse_raw_actors(event_dat) if a.actor_id == actor_id), None)
    if actor is None:
        raise ValueError(f"actor 0x{actor_id:08X} not in the event DAT")
    blobs = None
    if dialog_dat:
        blobs, _ = xi_dialog.raw_entry_blobs(dialog_dat)
    return lint_actor(actor, blobs, only_event)

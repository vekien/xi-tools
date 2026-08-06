#!/usr/bin/env python3
"""Create the ``0x07`` scheduler routine a cutscene needs to play a clip.

A cutscene dispatches animation with event opcode ``0x2C SetAction``, which fires a
4-char action tag against the actor's *resident* resources. The client only ever
schedules **``0x07`` EffectRoutine** tags — never a raw ``0x2B`` skeleton clip. So a
freshly imported clip (``anim import``) is invisible to the cutscene author until a
routine wraps it: this module builds that routine.

Rather than synthesise the sec1/sec2/sec3 headers from scratch (fragile), it
**clones a clean single-clip routine already in the model** (e.g. ``corp`` — sec2 is
just a start marker + one ``0x05`` play + end) and retargets three fixed fields: the
section name (the new action tag), the ``0x05`` command's clip DatId (``+8``), its
``duration`` (``+6``), and ``maxLoops`` (``+30``: ``0`` = loop forever, ``1`` = play
once then hold). The new section is spliced in right after the donor so it shares the
donor's directory scope and its clip ref resolves the same way.

See ``docs/fx/effect_system.md`` §3 and ``docs/cutscene_authoring.md``.
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from xi.common.xi_section import set_section_size
from xi.xi_config import editable_dat, read_path_for
from xi.entity.anim.xi_export import (
    parse_sections, read_animation_header, GAME_FPS,
    SECTION_TYPE_SKELETON_ANIMATION,
)

ROUTINE_TYPE = 0x07


def _norm_routine_tag(tag: str) -> str:
    """A 4-char routine/action tag. A digit-less short name is slot-numbered so it fills
    four bytes cleanly (``tlk`` -> ``tlk0``) instead of being space-padded (``tlk ``) —
    the padded form is non-standard and trips exact-string tag matching in the editor."""
    tag = tag.rstrip("\x00 ")
    if tag and not tag[-1].isdigit():
        tag = tag + "0"
    return tag[:4]


def _name4(tag: str) -> bytes:
    """A 4-byte DatId (space-padded, matching how routine/clip names are stored)."""
    b = tag.encode("ascii", "replace")[:4]
    if len(b) > 4:
        raise ValueError(f"tag {tag!r} is longer than 4 characters")
    return b.ljust(4, b" ")


def _clip_ref(clip_name: str) -> bytes:
    """The DatId a routine uses to reference a clip — a parameterised ``'xxx?'`` the
    client resolves to the concrete slot, and which ``_match_clip`` resolves by the
    literal prefix before the ``?``:

    * trailing slot digit → replace it: ``tlk0`` -> ``tlk?``, ``at00`` -> ``at0?``
    * short name (< 4 chars) → append ``?``: ``tlk`` -> ``tlk?`` (a digit-less layered
      track still resolves)
    * full 4-char non-digit name → referenced verbatim (exact match)."""
    raw = clip_name.rstrip("\x00 ")[:4]
    if not raw:
        return b"    "
    if raw[-1].isdigit():
        stem = raw[:-1]
    elif len(raw) < 4:
        stem = raw
    else:
        return raw.encode("ascii", "replace")
    return (stem + "?").encode("ascii", "replace")[:4].ljust(4, b" ")


@dataclass
class Donor:
    off: int
    size: int
    tag: str
    cmd_pos: int          # byte offset of the 0x05 command within the section
    entry_len: int        # length of that command entry


def find_clean_donor(data: bytes) -> Optional[Donor]:
    """A ``0x07`` routine whose sec2 is only markers + a single ``0x05`` clip play (no
    sub-routine/sound/vfx refs to drag along). Prefers the simplest (fewest commands)
    so the cloned routine does nothing but play the clip."""
    from xi.event.xi_event import _scene_sections, _routine_sec2_commands

    best: Optional[Tuple[int, Donor]] = None
    for off, tag, tc, size in _scene_sections(data):
        if tc != ROUTINE_TYPE:
            continue
        cmds = _routine_sec2_commands(data, tag)
        if not cmds:
            continue
        ops = [c["op"] for c in cmds]
        if ops.count(0x05) != 1 or any(o not in (0x00, 0x01, 0x05) for o in ops):
            continue
        c05 = next(c for c in cmds if c["op"] == 0x05)
        cmd_pos = 16 + c05["off"]                        # header is 16 bytes
        n = struct.unpack_from("<H", data, off + cmd_pos + 1)[0] & 0x1F
        entry_len = max(1, n) * 4
        donor = Donor(off=off, size=size, tag=tag, cmd_pos=cmd_pos, entry_len=entry_len)
        if best is None or len(cmds) < best[0]:
            best = (len(cmds), donor)
    return best[1] if best else None


def clip_duration_frames(data: bytes, clip_name: str) -> float:
    """A clip's in-game length in game-frames: ``(numFrames - 1) / keyFrameDuration``
    (the same length the emote-routine sizing uses)."""
    section = next((s for s in parse_sections(data)
                    if s.type_code == SECTION_TYPE_SKELETON_ANIMATION
                    and s.name.rstrip("\x00 ").lower() == clip_name.rstrip("\x00 ").lower()), None)
    if section is None:
        raise ValueError(f"clip {clip_name!r} not found in the DAT")
    _nj, num_frames, kdur = read_animation_header(data, section)
    return (num_frames - 1) / kdur if kdur else float(max(0, num_frames - 1))


def _resolve_max_loops(loop: Optional[bool] = True,
                       max_loops: Optional[int] = None) -> int:
    """``maxLoops`` is a full u16: ``0`` = forever, ``N`` = play N times then hold.
    ``--loops N`` wins when set; else ``--loop`` → 0 / ``--no-loop`` → 1."""
    if max_loops is not None:
        return max(0, min(0xFFFF, int(max_loops)))
    if loop is None:
        raise ValueError("loop mode unspecified")
    return 0 if loop else 1


def _fmt_loops(v: int) -> str:
    return "∞" if v == 0 else f"x{v}"


def build_routine_section(data: bytes, routine_tag: str, clip_name: str,
                          loop: bool = True, dur: Optional[int] = None,
                          trans_in: int = 15, trans_out: int = 15,
                          max_loops: Optional[int] = None) -> Tuple[bytes, dict]:
    """Clone the model's cleanest single-clip routine and retarget it to ``clip_name``.
    Returns ``(section_bytes, info)``.

    ``trans_in`` / ``trans_out`` are the ``0x05`` command's crossfade fields (frames,
    ``+24`` / ``+28``) — the client's animation BLENDING. Retail routines use 8-20
    (this rig's own ati0/atf0 are 10/10, dead fades in over 20); 0 = hard snap. The
    donor is often a snap-style routine (corp/gurd pose instantly by design), so the
    clone must SET these — inheriting the donor's 0/0 made every scheduled custom
    clip pop instead of blending from the idle.

    ``max_loops`` is the format's u16 at ``+30`` (``0`` = forever, ``N`` = play N
    times then hold; retail cast uses e.g. x28). When omitted, ``loop`` maps to
    ``0`` / ``1``.
    """
    donor = find_clean_donor(data)
    if donor is None:
        raise ValueError(
            "No clean single-clip routine to clone in this DAT — it has only complex "
            "routines (sub-routines / sounds / vfx). Cloning those would drag their "
            "extra commands along, so schedule creation is refused here.")

    clone = bytearray(data[donor.off: donor.off + donor.size])
    cp = donor.cmd_pos

    if dur is None:
        dur = round(2.0 * clip_duration_frames(data, clip_name))     # 2x length = natural speed
    dur = max(1, min(0xFFFF, int(dur)))
    ml = _resolve_max_loops(loop=loop, max_loops=max_loops)
    trans_in = max(0, min(0xFFFF, int(trans_in)))
    trans_out = max(0, min(0xFFFF, int(trans_out)))

    clone[0:4] = _name4(routine_tag)
    clone[cp + 8: cp + 12] = _clip_ref(clip_name)
    struct.pack_into("<H", clone, cp + 6, dur)
    if donor.entry_len >= 32:            # 0x05 SkeletonAnimation: transIn +24 / transOut +28 / maxLoops +30
        struct.pack_into("<H", clone, cp + 24, trans_in)
        struct.pack_into("<H", clone, cp + 28, trans_out)
        struct.pack_into("<H", clone, cp + 30, ml)

    info = {"donor": donor.tag, "tag": routine_tag, "clip": clip_name,
            "ref": clone[cp + 8: cp + 12].decode("ascii", "replace"),
            "dur": dur, "loop": ml == 0, "maxLoops": ml,
            "transIn": trans_in, "transOut": trans_out, "size": len(clone)}
    return bytes(clone), info


@dataclass
class ScheduleStep:
    """One clip in a chained routine. ``None`` fields auto-resolve (:func:`resolve_steps`):
    ``delay`` = previous step's ``dur`` (chain starts when the previous play-through
    ends; first step = 0), ``dur`` = 2 × clip length (natural speed), ``max_loops`` =
    play once + hold (the last step loops forever when ``loop_last``), blends = crossfade
    in on the first step / out on the last, hard-cut at the joins (poses align there —
    the retail ``ssit`` sit-down → sit-idle idiom, ``mi2`` 10/0 → ``mi3`` 0/20)."""
    clip: str
    delay: Optional[int] = None
    dur: Optional[int] = None
    max_loops: Optional[int] = None
    trans_in: Optional[int] = None
    trans_out: Optional[int] = None


def resolve_steps(data: bytes, steps: List[ScheduleStep], loop_last: bool = True,
                  blend: int = 15) -> List[dict]:
    """Fill every ``None`` field with the chain defaults (see :class:`ScheduleStep`) →
    ``[{clip, delay, dur, maxLoops, transIn, transOut, startAt}]``. ``startAt`` is the
    accumulated delay (the command stream's delays are relative to the previous command,
    xim ``EffectRoutineInstance.runEffects``)."""
    if not steps:
        raise ValueError("a schedule needs at least one clip")
    resolved, start_at = [], 0
    for i, s in enumerate(steps):
        first, last = i == 0, i == len(steps) - 1
        dur = s.dur if s.dur is not None else round(2.0 * clip_duration_frames(data, s.clip))
        dur = max(1, min(0xFFFF, int(dur)))
        delay = s.delay if s.delay is not None else (0 if first else resolved[-1]["dur"])
        delay = max(0, min(0xFFFF, int(delay)))
        ml = (max(0, min(0xFFFF, int(s.max_loops))) if s.max_loops is not None
              else (0 if (last and loop_last) else 1))
        ti = s.trans_in if s.trans_in is not None else (blend if first else 0)
        to = s.trans_out if s.trans_out is not None else (blend if last else 0)
        start_at += delay
        resolved.append({"clip": s.clip, "delay": delay, "dur": dur, "maxLoops": ml,
                         "transIn": max(0, min(0xFFFF, int(ti))),
                         "transOut": max(0, min(0xFFFF, int(to))), "startAt": start_at})
    return resolved


def build_chain_routine_section(data: bytes, routine_tag: str,
                                resolved: List[dict]) -> Tuple[bytes, dict]:
    """Clone the donor routine and replace its single ``0x05`` play command with one per
    step — a multi-clip chain (stand → sit-down → sit-idle …). Returns
    ``(section_bytes, info)``.

    The extra entries grow sec2, so the header's sec1/sec2/sec3 offsets past the splice
    shift by the growth, ``totalDelay`` (``+0x1C``) is restamped to the sum of the sec2
    delays (the retail invariant — every multi-clip routine surveyed sums exactly), and
    the section is re-padded to the 16-byte size granularity."""
    donor = find_clean_donor(data)
    if donor is None:
        raise ValueError(
            "No clean single-clip routine to clone in this DAT — it has only complex "
            "routines (sub-routines / sounds / vfx). Cloning those would drag their "
            "extra commands along, so schedule creation is refused here.")
    if donor.entry_len < 32:
        raise ValueError(f"donor {donor.tag!r}'s play command is too short to carry "
                         f"blend/loop fields ({donor.entry_len} bytes).")

    clone = bytearray(data[donor.off: donor.off + donor.size])
    cp, el = donor.cmd_pos, donor.entry_len
    template = bytes(clone[cp: cp + el])

    entries = bytearray()
    for r in resolved:
        e = bytearray(template)
        struct.pack_into("<H", e, 4, r["delay"])
        struct.pack_into("<H", e, 6, r["dur"])
        e[8:12] = _clip_ref(r["clip"])
        struct.pack_into("<H", e, 24, r["transIn"])
        struct.pack_into("<H", e, 28, r["transOut"])
        struct.pack_into("<H", e, 30, r["maxLoops"])
        entries += e

    growth = len(entries) - el
    out = bytearray(clone[:cp] + entries + clone[cp + el:])

    # Header fixups: the routine's sec1/sec2/sec3 offsets and totalDelay sit AFTER the
    # 16-byte section header + 16 zero bytes → buffer 0x20/0x24/0x28/0x2C. The stored
    # offsets are section-relative (header included), same base as cp.
    for hoff in (0x20, 0x24, 0x28):
        v = struct.unpack_from("<I", out, hoff)[0]
        if v > cp:
            struct.pack_into("<I", out, hoff, v + growth)
    struct.pack_into("<I", out, 0x2C, sum(r["delay"] for r in resolved))

    out += b"\x00" * (-len(out) % 16)
    meta = struct.unpack_from("<I", out, 4)[0]
    struct.pack_into("<I", out, 4,
                     set_section_size(meta, len(out), what="chained routine section"))
    out[0:4] = _name4(routine_tag)

    info = {"donor": donor.tag, "tag": routine_tag, "steps": resolved,
            "totalDelay": sum(r["delay"] for r in resolved), "size": len(out)}
    return bytes(out), info


def _splice_routine(data: bytes, tag: str, section: bytes) -> Tuple[bytes, str]:
    """Replace the existing routine named ``tag``, else insert after the donor (so the
    new routine shares its directory scope and clip refs resolve the same way)."""
    from xi.event.xi_event import _scene_sections
    existing = next(((off, size) for off, t, tc, size in _scene_sections(data)
                     if tc == ROUTINE_TYPE and t == tag), None)
    if existing is not None:
        off, size = existing
        return data[:off] + section + data[off + size:], "replaced"
    donor = find_clean_donor(data)
    pos = donor.off + donor.size
    return data[:pos] + section + data[pos:], "inserted"


def _assert_clips_present(data: bytes, clips: List[str], dat_name: str) -> None:
    sections = parse_sections(data)
    have = {s.name.rstrip("\x00 ").lower() for s in sections
            if s.type_code == SECTION_TYPE_SKELETON_ANIMATION}
    missing = [c for c in clips if c.rstrip("\x00 ").lower() not in have]
    if missing:
        listing = ", ".join(sorted({s.name.rstrip("\x00 ") for s in sections
                                    if s.type_code == SECTION_TYPE_SKELETON_ANIMATION})) or "(none)"
        raise ValueError(f"clip(s) {', '.join(repr(m) for m in missing)} not found in "
                         f"{dat_name}. Import first (anim import). Present clips: {listing}.")


def _echo_chain(resolved: List[dict], echo=print) -> None:
    """The chain plan, one line per clip: start time (accumulated delay, ≈seconds at
    the 2×-frames unit → /60), play window, loop mode and blends."""
    for i, r in enumerate(resolved, 1):
        echo(f"  {i}. {r['clip']:<6} starts @{r['startAt']:>4} (~{r['startAt'] / 60:.1f}s)  "
             f"window {r['dur']}  {_fmt_loops(r['maxLoops'])}  "
             f"blend {r['transIn']}/{r['transOut']}f")


def create_schedule(dat_path: Path, routine_tag: str, steps: List[ScheduleStep],
                    loop_last: bool = True, blend: int = 15, echo=print) -> dict:
    """Inject a chained ``0x07`` routine that plays ``steps`` in sequence (each clip's
    delay chains off the previous play window), so a cutscene can ``SetAction`` a
    transition-then-loop motion (sit-down → sitting idle). Additive like ``add``."""
    target = editable_dat(Path(dat_path), fresh=False)
    data = target.read_bytes()
    _assert_clips_present(data, [s.clip for s in steps], Path(dat_path).name)

    tag = _norm_routine_tag(routine_tag)
    resolved = resolve_steps(data, steps, loop_last=loop_last, blend=blend)
    section, info = build_chain_routine_section(data, tag, resolved)

    out, action = _splice_routine(data, tag, section)
    target.write_bytes(out)
    info.update(action=action, out_path=str(target))

    echo(f"{'Replaced' if action == 'replaced' else 'Created'} schedule {tag!r} — "
         f"{len(resolved)} clip chain (cloned from {info['donor']!r}):")
    _echo_chain(resolved, echo)
    echo(f"Shows in the cutscene author's Anim dropdown as {tag!r} (hard-refresh the "
         f"editor). The editor's 3D preview shows the first clip only — the full chain "
         f"plays in-game.")
    return info


def add_schedule(dat_path: Path, clip_name: str, routine_tag: Optional[str] = None,
                 loop: bool = True, dur: Optional[int] = None, no_base: bool = False,
                 trans_in: int = 15, trans_out: int = 15,
                 max_loops: Optional[int] = None,
                 echo=print) -> dict:
    """Inject a ``0x07`` routine (named ``routine_tag``, default = the clip name) that
    plays ``clip_name`` into ``dat_path``, so a cutscene can ``SetAction`` it."""
    target = editable_dat(Path(dat_path), fresh=not no_base)
    data = target.read_bytes()
    _assert_clips_present(data, [clip_name], Path(dat_path).name)

    tag = _norm_routine_tag(routine_tag or clip_name)
    section, info = build_routine_section(data, tag, clip_name, loop=loop, dur=dur,
                                          trans_in=trans_in, trans_out=trans_out,
                                          max_loops=max_loops)

    # Does a routine of this tag already exist? Replace it; else splice after the donor.
    out, info["action"] = _splice_routine(data, tag, section)

    target.write_bytes(out)
    info["out_path"] = str(target)

    echo(f"{'Replaced' if info['action'] == 'replaced' else 'Created'} schedule: {tag!r} "
         f"— plays clip {clip_name!r} (ref {info['ref']!r}), "
         f"{_fmt_loops(info['maxLoops'])}, dur {info['dur']}, "
         f"blend in/out {info['transIn']}/{info['transOut']}f (cloned from {info['donor']!r})")
    echo(f"Shows in the cutscene author's Anim dropdown as {tag!r} (hard-refresh the editor).")
    return info


def edit_schedule(dat_path: Path, tag: str,
                  trans_in: Optional[int] = None, trans_out: Optional[int] = None,
                  loop: Optional[bool] = None, dur: Optional[int] = None,
                  max_loops: Optional[int] = None,
                  echo=print) -> dict:
    """Patch an EXISTING routine's ``0x05`` play-command fields in place — blend
    (transIn/transOut), loop count, playback window — without re-cloning. Byte-surgical
    (u16 pokes at fixed offsets, section size unchanged), so it works on any routine,
    including the model's retail ones (``ati0``/``dead``/…). Only the fields passed are
    changed. A routine with SEVERAL play commands accepts blend edits (applied to all)
    but refuses ``--dur``/``--loop``/``--loops`` (ambiguous per-command)."""
    from xi.event.xi_event import _scene_sections, _routine_sec2_commands

    target = editable_dat(Path(dat_path), fresh=False)
    data = bytearray(target.read_bytes())
    tag = tag.rstrip("\x00 ")[:4]
    sec = next(((off, size) for off, t, tc, size in _scene_sections(bytes(data))
                if tc == ROUTINE_TYPE and t == tag), None)
    if sec is None:
        have = ", ".join(sorted(t for _o, t, tc, _s in _scene_sections(bytes(data))
                                if tc == ROUTINE_TYPE)) or "(none)"
        raise ValueError(f"routine {tag!r} not found. Present routines: {have}.")
    off, _size = sec
    cmds = [c for c in _routine_sec2_commands(bytes(data), tag) if c["op"] == 0x05]
    if not cmds:
        raise ValueError(f"routine {tag!r} plays no clip directly (it chains a "
                         f"sub-routine) — edit the linked routine instead.")
    loops_touch = loop is not None or max_loops is not None
    if len(cmds) > 1 and (dur is not None or loops_touch):
        raise ValueError(f"routine {tag!r} has {len(cmds)} play commands — --dur/--loop/"
                         f"--loops would be ambiguous; only blend (--blend/--trans-*) "
                         f"can edit it.")

    before = {"transIn": cmds[0]["transIn"], "transOut": cmds[0]["transOut"],
              "dur": cmds[0]["dur"], "maxLoops": cmds[0]["maxLoops"]}
    resolved_ml = (_resolve_max_loops(loop=loop if loop is not None else True,
                                      max_loops=max_loops)
                   if loops_touch else None)
    for c in cmds:
        cp = off + 16 + c["off"]
        n = struct.unpack_from("<H", data, cp + 1)[0] & 0x1F
        if max(1, n) * 4 < 32:
            raise ValueError(f"routine {tag!r}'s play command is too short to carry "
                             f"blend/loop fields.")
        if trans_in is not None:
            struct.pack_into("<H", data, cp + 24, max(0, min(0xFFFF, int(trans_in))))
        if trans_out is not None:
            struct.pack_into("<H", data, cp + 28, max(0, min(0xFFFF, int(trans_out))))
        if dur is not None:
            struct.pack_into("<H", data, cp + 6, max(1, min(0xFFFF, int(dur))))
        if resolved_ml is not None:
            struct.pack_into("<H", data, cp + 30, resolved_ml)

    target.write_bytes(bytes(data))
    after = {"transIn": trans_in if trans_in is not None else before["transIn"],
             "transOut": trans_out if trans_out is not None else before["transOut"],
             "dur": dur if dur is not None else before["dur"],
             "maxLoops": resolved_ml if resolved_ml is not None else before["maxLoops"]}
    echo(f"Edited routine {tag!r}: blend {before['transIn']}/{before['transOut']}f "
         f"→ {after['transIn']}/{after['transOut']}f, dur {before['dur']} → {after['dur']}, "
         f"{_fmt_loops(before['maxLoops'])} → {_fmt_loops(after['maxLoops'])}"
         + (f"  ({len(cmds)} play commands patched)" if len(cmds) > 1 else ""))
    return {"tag": tag, "before": before, "after": after, "out_path": str(target)}


def copy_schedule(dat_path: Path, src_tag: str, dst_tag: str,
                  echo=print) -> dict:
    """Duplicate an existing ``0x07`` routine under a new tag (byte-identical clone,
    only the section name changes). Lets you fork a schedule and tweak the copy
    (``schedule edit``) without losing the original."""
    from xi.event.xi_event import _scene_sections

    target = editable_dat(Path(dat_path), fresh=False)
    data = target.read_bytes()
    src_tag = src_tag.rstrip("\x00 ")[:4]
    dst_tag = _norm_routine_tag(dst_tag)

    if src_tag == dst_tag:
        raise ValueError(f"source and destination tags are the same ({src_tag!r})")

    sections = list(_scene_sections(data))
    src = next(((off, size) for off, t, tc, size in sections
                if tc == ROUTINE_TYPE and t == src_tag), None)
    if src is None:
        have = ", ".join(sorted(t for _o, t, tc, _s in sections
                                if tc == ROUTINE_TYPE)) or "(none)"
        raise ValueError(f"routine {src_tag!r} not found. Present routines: {have}.")
    src_off, src_size = src

    clone = bytearray(data[src_off: src_off + src_size])
    clone[0:4] = _name4(dst_tag)

    existing = next(((off, size) for off, t, tc, size in sections
                     if tc == ROUTINE_TYPE and t == dst_tag), None)
    if existing is not None:
        off, size = existing
        # If dest sits before src in the file, splicing dest first shifts src_off —
        # rebuild from the original bytes with dest replaced, then insert is N/A.
        out = data[:off] + bytes(clone) + data[off + size:]
        action = "replaced"
    else:
        # Splice immediately after the source so the copy shares its directory scope.
        pos = src_off + src_size
        out = data[:pos] + bytes(clone) + data[pos:]
        action = "inserted"

    target.write_bytes(out)
    info = {"src": src_tag, "tag": dst_tag, "size": src_size, "action": action,
            "out_path": str(target)}
    echo(f"{'Replaced' if action == 'replaced' else 'Copied'} schedule: "
         f"{src_tag!r} → {dst_tag!r} ({src_size} bytes)")
    echo(f"Shows in the cutscene author's Anim dropdown as {dst_tag!r} "
         f"(hard-refresh the editor).")
    return info


def list_schedules(dat_path: Path) -> List[dict]:
    """Every schedulable routine in the model DAT and the clip it plays
    (``[{tag, clip}]``) — the same list the cutscene author's Anim dropdown shows."""
    from xi.gear.xi_character import _model_motions
    return _model_motions(read_path_for(Path(dat_path)).read_bytes())


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402


@_click.group('schedule')
def group():
    """Create/list the 0x07 scheduler routines that let a cutscene play a clip.

    A cutscene fires animation via 0x2C SetAction, which can only schedule a routine
    tag — never a raw 0x2B clip. So after `anim import`ing a clip, wrap it in a routine
    here to make it selectable in the cutscene author's Anim dropdown.
    """
    pass


@group.command('add')
@_click.argument('dat_path')
@_click.option('--clip', required=True,
               help='The 0x2B clip the routine should play (e.g. tlk0).')
@_click.option('--tag', default=None,
               help='Routine/action tag the cutscene fires — up to 4 chars, default = the '
                    'clip name. A digit-less name is slot-numbered (tlk -> tlk0).')
@_click.option('--loop/--no-loop', default=True, show_default=True,
               help='Loop forever (maxLoops=0) vs play once and hold (maxLoops=1). '
                    'Overridden by --loops N.')
@_click.option('--loops', 'max_loops', type=int, default=None, metavar='N',
               help='maxLoops as a full u16: 0 = forever, N = play N times then hold '
                    '(retail cast uses e.g. 28). Overrides --loop/--no-loop.')
@_click.option('--dur', type=int, default=None,
               help='Playback window (u16). Default = 2x the clip length (natural speed).')
@_click.option('--blend', type=int, default=None, metavar='N',
               help='Crossfade in AND out, in frames (30/s) — shorthand for '
                    '--trans-in N --trans-out N. Retail routines use 8-20; 0 = hard snap. '
                    '[default: 15]')
@_click.option('--trans-in', type=int, default=None, metavar='N',
               help='Crossfade INTO the clip only — overrides --blend for the in side.')
@_click.option('--trans-out', type=int, default=None, metavar='N',
               help='Crossfade back OUT only — overrides --blend for the out side.')
def add_cmd(dat_path, clip, tag, loop, max_loops, dur, blend, trans_in, trans_out):
    """Wrap a clip in a 0x07 routine so a cutscene can SetAction it.

    Additive: operates on the DAT as it is now (the clip must already be there from
    `anim import`) — it does NOT restore .base first, so your imported clip is kept.
    Re-running with the same --tag replaces that routine.
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    base = blend if blend is not None else 15
    ti = trans_in if trans_in is not None else base
    to = trans_out if trans_out is not None else base
    try:
        add_schedule(dat, clip, routine_tag=tag, loop=loop, dur=dur,
                     trans_in=ti, trans_out=to, max_loops=max_loops,
                     no_base=True, echo=_click.echo)
    except ValueError as e:
        raise _click.ClickException(str(e))


def _wizard_collect(data: bytes, tag: Optional[str], loop_last: bool, blend: int):
    """The interactive flow behind ``schedule create``: pick a tag, pick clips in
    order, defaults for everything, optional per-clip tuning pass. Returns
    ``(tag, steps, loop_last)`` or ``None`` when aborted."""
    from xi.event.xi_event import _scene_sections

    clips = sorted({s.name.rstrip("\x00 ") for s in parse_sections(data)
                    if s.type_code == SECTION_TYPE_SKELETON_ANIMATION})
    routines = sorted(t for _o, t, tc, _s in _scene_sections(data) if tc == ROUTINE_TYPE)
    if not clips:
        raise _click.ClickException("this DAT has no 0x2B clips to schedule.")
    by_lower = {c.lower(): c for c in clips}

    _click.echo(f"Clips in this DAT:     {', '.join(clips)}")
    _click.echo(f"Existing routines:     {', '.join(routines) or '(none)'}\n")

    if tag is None:
        tag = _click.prompt("Routine tag (≤4 chars — what the cutscene fires)").strip()
    tag = _norm_routine_tag(tag)
    if not tag:
        raise _click.ClickException("a routine tag is required.")
    if tag in routines and not _click.confirm(
            f"Routine {tag!r} already exists — replace it?", default=False):
        return None

    steps: List[ScheduleStep] = []
    while True:
        label = (f"Clip {len(steps) + 1}" if not steps
                 else f"Clip {len(steps) + 1} (Enter to finish)")
        raw = _click.prompt(label, default="", show_default=False).strip()
        if not raw:
            if steps:
                break
            _click.echo("  at least one clip is needed.")
            continue
        clip = by_lower.get(raw.rstrip("\x00 ").lower())
        if clip is None:
            _click.echo(f"  no clip {raw!r} in this DAT. Clips: {', '.join(clips)}")
            continue
        steps.append(ScheduleStep(clip))

    loop_last = _click.confirm(
        "Loop the last clip forever (e.g. a sitting idle)?", default=loop_last)

    if _click.confirm("Tune per-clip timing/blend (delays auto-chain by default)?",
                      default=False):
        resolved = resolve_steps(data, steps, loop_last=loop_last, blend=blend)
        tuned = []
        for i, r in enumerate(resolved, 1):
            _click.echo(f"-- clip {i}: {r['clip']}")
            tuned.append(ScheduleStep(
                r["clip"],
                delay=_click.prompt("   delay (frames after the previous command)",
                                    type=int, default=r["delay"]),
                dur=_click.prompt("   window (frames; default = one natural play)",
                                  type=int, default=r["dur"]),
                max_loops=_click.prompt("   loops (0 = forever, N = play N× then hold)",
                                        type=int, default=r["maxLoops"]),
                trans_in=_click.prompt("   blend in (frames)", type=int,
                                       default=r["transIn"]),
                trans_out=_click.prompt("   blend out (frames)", type=int,
                                        default=r["transOut"])))
        steps = tuned

    return tag, steps, loop_last


@group.command('create')
@_click.argument('dat_path')
@_click.option('--tag', default=None,
               help='Routine/action tag the cutscene fires (≤4 chars). Wizard prompts '
                    'for it when omitted; required with --clip.')
@_click.option('--clip', 'clips', multiple=True, metavar='NAME',
               help='A clip to chain, in play order — repeat per clip. Skips the '
                    'wizard; timing/blends use the chain defaults.')
@_click.option('--loop-last/--no-loop-last', default=True, show_default=True,
               help='Loop the final clip forever (the sit-idle case) vs play it once '
                    'and hold the last frame.')
@_click.option('--blend', type=int, default=15, show_default=True, metavar='N',
               help='Crossfade into the first clip and out of the last, in frames '
                    '(30/s). Joins between chained clips hard-cut (their poses align).')
def create_cmd(dat_path, tag, clips, loop_last, blend):
    """Chain SEVERAL clips into one 0x07 routine — transition, then loop.

    The retail sit idiom (ssit: sit-down once → sitting idle forever), for your own
    clips: each clip starts when the previous play-through ends, earlier clips play
    once and hold, the last loops.

    \b
      xi anim schedule create rom9/25/40                          # wizard
      xi anim schedule create rom9/25/40 --tag sit0 --clip sitd --clip siti

    Additive like `add`: the clips must already be in the DAT (anim import), and
    re-using an existing --tag replaces that routine. Fine-tune afterwards with
    `schedule edit` (blend) or re-run create.
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    data = editable_dat(Path(dat), fresh=False).read_bytes()

    try:
        if clips:
            if tag is None:
                raise _click.ClickException("--tag is required with --clip.")
            steps = [ScheduleStep(c) for c in clips]
            create_schedule(dat, tag, steps, loop_last=loop_last, blend=blend,
                            echo=_click.echo)
            return

        picked = _wizard_collect(data, tag, loop_last, blend)
        if picked is None:
            _click.echo("Aborted — nothing written.")
            return
        tag, steps, loop_last = picked

        resolved = resolve_steps(data, steps, loop_last=loop_last, blend=blend)
        _click.echo(f"\nPlan for routine {tag!r}:")
        _echo_chain(resolved, _click.echo)
        if not _click.confirm(f"Write routine {tag!r} ({len(steps)} clip"
                              f"{'s' if len(steps) != 1 else ''})?", default=True):
            _click.echo("Aborted — nothing written.")
            return
        create_schedule(dat, tag, steps, loop_last=loop_last, blend=blend,
                        echo=_click.echo)
    except ValueError as e:
        raise _click.ClickException(str(e))
    except (_click.exceptions.Abort, EOFError):
        _click.echo("\nAborted — nothing written.")


@group.command('edit')
@_click.argument('dat_path')
@_click.argument('tag')
@_click.option('--blend', type=int, default=None, metavar='N',
               help='Crossfade in AND out, in frames (30/s) — shorthand for '
                    '--trans-in N --trans-out N. 0 = hard snap.')
@_click.option('--trans-in', type=int, default=None, metavar='N',
               help='Crossfade INTO the clip only — overrides --blend for the in side.')
@_click.option('--trans-out', type=int, default=None, metavar='N',
               help='Crossfade back OUT only — overrides --blend for the out side.')
@_click.option('--loop/--no-loop', default=None,
               help='Loop forever vs play once and hold. Unspecified = keep current. '
                    'Overridden by --loops N.')
@_click.option('--loops', 'max_loops', type=int, default=None, metavar='N',
               help='maxLoops as a full u16: 0 = forever, N = play N times then hold. '
                    'Overrides --loop/--no-loop. Unspecified = keep current.')
@_click.option('--dur', type=int, default=None,
               help='Playback window (u16). Unspecified = keep current.')
def edit_cmd(dat_path, tag, blend, trans_in, trans_out, loop, max_loops, dur):
    """Edit an EXISTING routine in place — blend / loops / duration.

    Byte-surgical: pokes the routine's 0x05 play-command fields without re-cloning,
    so it works on any routine (your scheduled clips AND the model's retail ones).

    \b
      xi anim schedule edit rom9/25/40 tlk0 --blend 5
      xi anim schedule edit rom9/25/40 tlk0 --loops 28
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    ti = trans_in if trans_in is not None else blend
    to = trans_out if trans_out is not None else blend
    if ti is None and to is None and loop is None and max_loops is None and dur is None:
        raise _click.ClickException(
            "nothing to change — pass --blend / --trans-in / --trans-out / "
            "--loop / --loops / --dur")
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    try:
        edit_schedule(dat, tag, trans_in=ti, trans_out=to, loop=loop, dur=dur,
                      max_loops=max_loops, echo=_click.echo)
    except ValueError as e:
        raise _click.ClickException(str(e))


@group.command('copy')
@_click.argument('dat_path')
@_click.argument('src_tag')
@_click.argument('dst_tag')
def copy_cmd(dat_path, src_tag, dst_tag):
    """Duplicate a routine under a new tag (fork, then edit the copy).

    \b
      xi anim schedule copy rom9/25/40 tlk0 tlk1
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    try:
        copy_schedule(dat, src_tag, dst_tag, echo=_click.echo)
    except ValueError as e:
        raise _click.ClickException(str(e))


@group.command('list')
@_click.argument('dat_path')
def list_cmd(dat_path):
    """List the schedulable routines in a model DAT and the clip each plays."""
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    rows = list_schedules(dat)
    if not rows:
        _click.echo("No schedulable routines found (model has no 0x07 routines).")
        return
    # Blend (transIn/transOut) per routine, straight from the 0x05 command bytes.
    # A multi-clip chain shows every clip (a→b), blend = in of the first / out of
    # the last, loops = the last clip's.
    from xi.event.xi_event import _routine_sec2_commands, _scene_sections, _match_clip
    data = read_path_for(Path(dat)).read_bytes()
    secs = _scene_sections(data)
    blend, chain = {}, {}
    for r in rows:
        c05s = [c for c in _routine_sec2_commands(data, r["tag"]) if c["op"] == 0x05]
        if not c05s:
            continue
        blend[r["tag"]] = (c05s[0]["transIn"], c05s[-1]["transOut"], c05s[-1]["maxLoops"])
        if len(c05s) > 1:
            chain[r["tag"]] = "→".join(
                (_match_clip(secs, c["ref"]) or (c["ref"] or "?").strip()) for c in c05s)
    wclip = max([10] + [len(chain.get(r["tag"], r["clip"] or "")) for r in rows])
    _click.echo(f"{'routine':>8}  {'plays clip':<{wclip}}  {'blend in/out':<12}  loops")
    _click.echo("  " + "-" * (34 + wclip))
    for r in rows:
        ti, to, ml = blend.get(r["tag"], ("?", "?", "?"))
        loops = _fmt_loops(ml) if isinstance(ml, int) else ml
        _click.echo(f"{r['tag']:>8}  {chain.get(r['tag'], r['clip']):<{wclip}}  "
                    f"{f'{ti}/{to}f':<12}  {loops}")
    _click.echo(f"\n{len(rows)} routine(s). These are what the cutscene author's Anim "
                f"dropdown lists. blend = crossfade frames (30/s); 0/0 snaps. "
                f"loops: ∞ = forever, xN = play N times then hold.")

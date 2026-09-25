"""What compiling a ``xi.cutscene.v1`` into a zone needs besides the compiler itself.

Shared by the ``zone_events`` action of ``xi dats`` and the zone editor's bridge:

- **camera scene ids**: a cutscene with a camera gets its own scene DAT, which the
  event's ``0x45`` opcodes reach through a ref value ``p``. Custom cameras take ids
  only in the safe mid band (``p`` 300–599, files 56941–57240). The high band (``p``
  600+, 70947+) crashes the client even with retail scene bytes.
- **where the scene DAT goes**: the placement a cutscene names (``ROM10/490/55.DAT``,
  or the editor's ``cameraDat`` fields), and the check that the path doesn't already
  hold someone else's file;
- **animation tags**: each cast NPC's own motion routines and the shared gesture bank's
  inventory. Tags are normalised against them before the compile
  (``xi_compile.normalize_cutscene_anim_tags``);
- **the lint gate**: the compiled event checked the way ``xi event cutscene compile``
  always has.
"""
from __future__ import annotations

from pathlib import Path

# ── camera scene ids ─────────────────────────────────────────────────────────

MID_BAND = (56941, 57240)          # p 300..599: file = 56641 + p


def scene_p_for(file_id: int) -> int | None:
    """Inverse of the 0x45 scene datid map: a scene DAT at ``file_id`` → the ref value
    ``p`` that reaches it, or ``None`` if the id isn't in a reachable band."""
    if 30704 <= file_id < 31004:           # tier p<300      : file = 30704 + p
        return file_id - 30704
    if 56941 <= file_id <= 57240:          # tier 300<=p<600 : file = 56641 + p
        return file_id - 56641
    if 70947 <= file_id <= 76000:          # tier p>=600     : file = 70347 + p
        return file_id - 70347
    return None


def camera_scene_id_safe(file_id) -> bool:
    """True if ``file_id`` is in the mid band we may use for custom cameras."""
    if file_id is None or isinstance(file_id, bool):
        return False
    try:
        p = scene_p_for(int(file_id))
    except (TypeError, ValueError):
        return False
    return p is not None and 300 <= p < 600


def has_camera_track(cutscene: dict) -> bool:
    """A camera exists if the legacy single 'camera' track OR the split Position sub-track
    ('campos', which defines the shots) carries keyframes, or the step list has camera
    steps. Post-Phase-3 the editor sends the three sub-tracks (campos/camrot/camzoom);
    older defs still send 'camera'."""
    tl = cutscene.get("timeline") or {}
    if tl.get("tracks"):
        return any(t.get("kind") in ("camera", "campos") and t.get("keyframes")
                   for t in (tl.get("tracks") or []))
    return any(s.get("op") == "camera" and s.get("shotSpec") for s in cutscene.get("steps") or [])


def free_camera_file_id(skip=()) -> int:
    """The lowest mid-band file id no table pair of the install registers (and not in
    ``skip``). Raises when the band is full."""
    from xi.ftable.xi_core import load_all_tables, resolve_dat
    tables = load_all_tables()
    skip = set(skip)
    for fid in range(MID_BAND[0], MID_BAND[1] + 1):
        if fid in skip:
            continue
        if not any(resolve_dat(fd, vd, fid)[0] for _, (fd, vd) in tables.items()):
            return fid
    raise RuntimeError(
        "no free camera-scene file id in the safe mid band (p 300..599 / file 56941..57240). "
        "Free a mid-band slot or expand — high-tier 71k ids crash the client.")


# ── where the scene DAT goes ─────────────────────────────────────────────────

def rom_folder(vt: int) -> str:
    """Disk folder for a VTABLE version byte: 1 → 'ROM', else 'ROM{vt}'."""
    return "ROM" if vt == 1 else f"ROM{vt}"


def _check_placement(vt: int, subdir: int, slot: int) -> None:
    if not (1 <= vt <= 10):
        raise ValueError(f"Camera DAT ROM {vt} out of range (1..10).")
    if not (0 <= subdir <= 511):
        raise ValueError(f"Camera DAT Path {subdir} out of range (0..511).")
    if not (0 <= slot <= 127):
        raise ValueError(f"Camera DAT filename slot {slot} out of range (0..127).")


def parse_camera_dat(cutscene: dict) -> tuple[int, int, int] | None:
    """Parse the editor's Camera DAT fields → ``(vt, subdir, slot)``, or ``None`` when the
    cutscene has no camera track (nothing to place). Raises ``ValueError`` with a user-facing
    message when a camera track exists but the fields are missing/invalid — Publish is gated
    on this."""
    if not has_camera_track(cutscene):
        return None
    cd = cutscene.get("cameraDat") or {}
    rom_raw = str(cd.get("rom", "")).strip().upper()
    if rom_raw.startswith("ROM"):
        rom_raw = rom_raw[3:].strip()
    path_raw = str(cd.get("path", "")).strip().strip("/\\")
    file_raw = str(cd.get("file", "")).strip()
    if not rom_raw or not path_raw or not file_raw:
        raise ValueError(
            "Set the Camera DAT (ROM, Path, Dat Filename) in Settings before publishing a "
            "cutscene with a camera.")
    try:
        vt = int(rom_raw)
    except ValueError:
        raise ValueError(f"Camera DAT ROM must be a number like 10, got {cd.get('rom')!r}.")
    # Path is the numeric subdir (tolerate a user pasting e.g. 'ROM10/490' — take the last part).
    try:
        subdir = int(path_raw.replace("\\", "/").split("/")[-1])
    except ValueError:
        raise ValueError(f"Camera DAT Path must be a folder number like 490, got {path_raw!r}.")
    try:
        slot = int(Path(file_raw).stem)
    except ValueError:
        raise ValueError(f"Camera DAT filename must be like 50.dat, got {file_raw!r}.")
    _check_placement(vt, subdir, slot)
    return vt, subdir, slot


def camera_dat_rel(cutscene: dict) -> str | None:
    """The editor's Camera DAT fields as a ROM path (``ROM10/490/55.DAT``); None when unset."""
    try:
        got = parse_camera_dat(cutscene)
    except ValueError:
        return None
    if got is None:
        return None
    vt, subdir, slot = got
    return f"{rom_folder(vt)}/{subdir}/{slot}.DAT"


def camera_path_collision(roots, rel: str, file_id: int) -> str | None:
    """Refuse a camera placement that would overwrite something that isn't a camera scene.

    If the path already holds an entity mesh (or anything that isn't an ``evte`` scene),
    the copy placed there shadows the real DAT and the model vanishes in-game. That's how
    Battle Worn Byakko (ROM10/25/40.DAT) went invisible after a cutscene publish pointed
    its camera there. Returns a user-facing error, or ``None`` when the path is free or
    already a camera scene."""
    for root in roots:
        if root is None:
            continue
        p = Path(root) / Path(*rel.split("/"))
        if not p.is_file():
            continue
        try:
            head = p.read_bytes()[:4]
        except OSError:
            continue
        if head == b"evte":          # a camera scene (ours, or a previous publish of the slot)
            continue
        kind = head.decode("ascii", "replace") if head else "?"
        return (f"Camera DAT {rel} already holds a non-camera file ({kind!r}, {p.stat().st_size} bytes "
                f"at {p}). Pick a free placement — overwriting it would hide the model that lives there "
                f"(file id {file_id} would shadow it).")
    return None


# ── animation tags ───────────────────────────────────────────────────────────

def default_look_rows(ids) -> dict:
    """``{npcid: {name, look(bytes), …}}`` the way the zone editor resolves them: its
    custom-NPC registry, the live ``npc_list``, then the bundled snapshot."""
    from xi.zone.xi_bridge import _npc_look_rows
    return _npc_look_rows(list(ids))


def cast_motion_maps(cutscene: dict, look_rows=None) -> dict:
    """Per-cast schedulable-motion maps for tag normalisation →
    ``{castId: {"valid": set(routineTag), "clip2routine": {clipTag: routineTag}, "name"}}``.

    Built from each fixed-model cast NPC's model DAT (its 0x07 routines + resolved clips,
    via :func:`list_look_animations`). A cast entry's own ``look`` wins; the rest come from
    ``look_rows(ids)`` (default: :func:`default_look_rows`). Equipped-look cast members are
    omitted — their gestures ride the shared bank and their tags pass through untouched.
    '@'-prefixed system routines (auto-turn @tl0/@tr0) stay VALID but never capture a clip
    mapping — @tl0 references wlk?/idl? clips internally, and mapping 'wlk0'→'@tl0' would
    turn a walk keyframe into a turn-in-place."""
    out = {}
    cast = ((cutscene.get("cast") or {}).get("cast")) or []
    wanted, inline = [], {}
    for c in cast:
        ent = c.get("entity")
        if not ent or ent == "player":
            continue
        try:
            aid = ent if isinstance(ent, int) else int(str(ent).replace("0x", "").replace("0X", ""), 16)
        except ValueError:
            continue
        wanted.append((c.get("id"), aid, c.get("name")))
        if isinstance(c.get("look"), str):
            try:
                inline[aid] = {"look": bytes.fromhex(c["look"][2:])}
            except ValueError:
                pass
    if not wanted:
        return out
    need = [aid for _cid, aid, _n in wanted if aid not in inline]
    rows = dict(inline)
    if need:
        try:
            rows.update((look_rows or default_look_rows)(need))
        except Exception:
            pass
    from xi.gear.xi_character import list_look_animations
    for cid, aid, name in wanted:
        row = rows.get(aid)
        if not row:
            continue
        try:
            r = list_look_animations(row["look"])
        except Exception:
            continue
        if not r.get("ok") or r.get("type") == "equipped":
            continue
        motions = r.get("motions") or []
        c2r = {}
        for m in motions:
            if not str(m["tag"]).startswith("@"):
                c2r.setdefault(m["clip"], m["tag"])
        out[cid] = {"valid": {m["tag"] for m in motions},
                    "clip2routine": c2r, "name": name or cid}
    return out


_GESTURE_BANK_TAGS_CACHE: dict = {}


def gesture_bank_tags(bank: int = 60) -> frozenset:
    """Routine tags ACTUALLY present in the shared gesture bank DAT — ground truth for
    the compiler's 0x5B-vs-0x2C dispatch.

    0x5B ReadEventMotionRes loads file ``32104 + bank`` (default bank 60 → 32164, the
    humanoid talk/think/bow set) onto the entity and plays the tag from it. The compiler
    used to trust its hardcoded ``_GESTURE_TAGS`` mirror of this file, which drifts (the
    docstrings mention 'han0' yet the mirror omits it → a han0 keyframe no-oped). Parsing
    the bank's 0x07 sections gives the real inventory. Cached per bank; empty frozenset on
    any failure — the compiler then falls back to ``_GESTURE_TAGS``."""
    if bank in _GESTURE_BANK_TAGS_CACHE:
        return _GESTURE_BANK_TAGS_CACHE[bank]
    tags = frozenset()
    try:
        from xi.xi_config import FFXI_DIR, read_path_for
        from xi.ftable.xi_core import scan_file_ids
        from xi.event.xi_event import _scene_sections
        fid = 32104 + bank
        # ☠ scan_file_ids compacts its result — match on file_id, never positionally.
        hit = next((h for h in scan_file_ids([fid]) if h.get("file_id") == fid), None)
        if hit:
            data = read_path_for(Path(FFXI_DIR) / hit["dat"]).read_bytes()
            tags = frozenset(t for _o, t, tc, _s in _scene_sections(data) if tc == 0x07)
    except Exception:
        tags = frozenset()
    _GESTURE_BANK_TAGS_CACHE[bank] = tags
    return tags


def prepare_anim(cutscene: dict, look_rows=None) -> tuple[dict, frozenset, list[str]]:
    """Normalise ``cutscene``'s animation tags in place before the compile →
    ``(cast_motions, bank_tags, warnings)`` to pass on to ``compile_cutscene``.

    Non-gesture tags emit as 0x2C SetAction, which only fires the actor's OWN 0x07
    routines: legacy defs stored raw 0x2B clip ids (at00/btl0) that silently no-op in
    game, so a clip is rewritten to the routine that owns it where the model allows. The
    same maps go to the compiler, so an OWN routine outranks a same-named curated gesture
    (a custom 'tlk0' on a monster rig must not ride the 0x5B humanoid bank)."""
    from xi.event import xi_compile
    cast_motions = cast_motion_maps(cutscene, look_rows)
    bank_tags = gesture_bank_tags(int((cutscene.get("flags") or {}).get("animBank") or 60))
    warnings = xi_compile.normalize_cutscene_anim_tags(cutscene, cast_motions, bank_tags=bank_tags)
    return cast_motions, bank_tags, list(warnings)


# ── cast names ───────────────────────────────────────────────────────────────

# npc_list.namevis = the HIGH byte of packet 0x000E Flags3 (XiPackets): bit 0x20
# (packet bit 29) = "health bar hidden and the name above their head not rendered".
# ☠ NOT 0x08 (packet bit 27) — that's "untargetable + name hidden under specific
# conditions" and verified in-game to NOT hide cutscene cast names. Retail zone-243
# rows agree: Survival_Guide / Proto-Waypoint (no floating name) carry 0x20.
NAMEVIS_HIDE = 0x20


# ── the owner and the lint gate ──────────────────────────────────────────────

def owner_entity(cutscene: dict) -> int | None:
    """The entity id of the cast member that owns the event, or None."""
    from xi.event import xi_compile
    for c in (cutscene.get("cast") or {}).get("cast") or []:
        if c.get("id") == cutscene.get("actor"):
            try:
                return xi_compile._resolve_entity(c.get("entity"))
            except xi_compile.CutsceneCompileError:
                return None
    return None


def lint(event_dat: bytes, dialog_dat: bytes | None, owner: int, event_id: int) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` of the compiled event: sizes, jumps, selectors, message ids,
    menu markers (``xi.event.xi_lint``)."""
    from xi.event import xi_lint
    errors, warnings = [], []
    for _eid, lr in xi_lint.lint_dat(event_dat, dialog_dat, owner, event_id).items():
        warnings += lr.warnings
        errors += lr.errors
    return errors, warnings

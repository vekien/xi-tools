"""``xi ability inspect`` (xi.ability.xi_inspect): flattening a 0x07 routine graph into
absolute frames, following local links with the inherited clock, and resolving clip /
sound / generator references.

The synthetic test packs a tiny DAT by hand; the install-backed tests use the ``root``
fixture and skip without FFXI_DIR."""
import struct
from pathlib import Path

import pytest

from xi.ability import xi_inspect as ai


def _section(name: bytes, type_code: int, body: bytes) -> bytes:
    body = body + b"\0" * (-len(body) % 16)
    size = 16 + len(body)
    meta = type_code | ((size // 16) << 7)
    return name.ljust(4, b"\0") + struct.pack("<I", meta) + b"\0" * 8 + body


def _cmd(op: int, delay: int, dur: int, ref: bytes = b"", extra: bytes = b"") -> bytes:
    body = ref.ljust(4, b"\0") if ref else b""
    body += extra
    total = 8 + len(body)
    total += -total % 4
    n = total // 4
    return bytes([op]) + struct.pack("<H", n) + b"\0" + struct.pack("<HH", delay, dur) + body.ljust(total - 8, b"\0")


def _routine(cmds: bytes, total: int) -> bytes:
    """16 zero bytes, sec1/sec2/sec3 offsets (section-relative, i.e. +16 for the header),
    totalDelay, then sec1 = just an end marker, sec2 = the commands."""
    sec1 = b"\x00\x01\x00\x00"
    hdr_len = 0x20
    sec1_off = 16 + hdr_len
    sec2_off = sec1_off + len(sec1)
    sec3_off = sec2_off + len(cmds)
    return (b"\0" * 16 + struct.pack("<4I", sec1_off, sec2_off, sec3_off, total)
            + sec1 + cmds + b"\x00\x01\x00\x00")


def _sound(name: bytes, sound_id: int) -> bytes:
    return _section(name, ai.T_SOUND, b"SeSep  " + b"\0" + struct.pack("<I", sound_id))


@pytest.fixture
def synthetic(tmp_path: Path) -> Path:
    # main: lock @0, clip b00? @10 (blend 20/0, x1), link 'hit1' @35, sound @40, end
    # hit1: gen g000 @+0, gen g001 @+5 (dur 10), end  -> absolute 35 and 40
    # (a delay is the wait AFTER its command: lock waits 10, clip 25, link 5, sound 20
    # out to the routine's total of 60)
    # 0x05 args after the ref: 2 floats @+12/+16, pad @+20, transIn u16 @+24, pad,
    # transOut u16 @+28, maxLoops u16 @+30 (xim EffectRoutineParser 0x05)
    play = _cmd(0x05, 25, 35, b"b00?",
                struct.pack("<ff", 1.0, 0.0) + b"\0" * 4 + struct.pack("<HHHH", 20, 0, 0, 1))
    main = (_cmd(0x01, 0, 0) + _cmd(0x20, 10, 100) + play + _cmd(0x03, 5, 0, b"hit1")
            + _cmd(0x0A, 20, 0, b"8050") + _cmd(0x00, 0, 0))
    hit1 = (_cmd(0x01, 0, 0) + _cmd(0x02, 5, 0, b"g000") + _cmd(0x02, 10, 10, b"g001")
            + _cmd(0x00, 0, 0))
    gen = struct.pack("<H", 0x0002) + b"\0" * 0xF0     # attachFlags @ data+0 = TargetActor
    dat = (_section(b"root", ai.T_DIR, b"") + _section(b"main", ai.T_ROUTINE, _routine(main, 60))
           + _section(b"hit1", ai.T_ROUTINE, _routine(hit1, 15))
           + _section(b"g000", ai.T_GEN, gen) + _section(b"g001", ai.T_GEN, gen)
           + _sound(b"8050", 18050) + _section(b"end", ai.T_END, b""))
    p = tmp_path / "synth.DAT"
    p.write_bytes(dat)
    return p


def test_flatten_follows_local_links_with_inherited_clock(synthetic: Path):
    m = ai.Model.load(synthetic)
    ev = {(e["op"], e["ref"]): e for e in ai.flatten(m, "main")}
    assert ev[(0x05, "b00?")]["start"] == 10
    assert ev[(0x05, "b00?")]["detail"]["blend"] == [20, 0]
    assert ev[(0x05, "b00?")]["detail"]["in_dat"] is False      # no clips in this DAT
    assert ev[(0x03, "hit1")]["start"] == 35
    assert ev[(0x02, "g000")]["start"] == 35 and ev[(0x02, "g000")]["via"] == ["main"]
    assert ev[(0x02, "g001")]["start"] == 40 and ev[(0x02, "g001")]["dur"] == 10
    assert ev[(0x02, "g001")]["detail"]["generator"]["attach"] == "TargetActor"
    assert ev[(0x0A, "8050")]["start"] == 40
    assert ev[(0x0A, "8050")]["detail"]["sound"]["sound_id"] == 18050


def test_inspect_reports_totals_and_reached_routines(synthetic: Path):
    info = ai.inspect_target(ai.Target("synth", synthetic, "synth.DAT"))
    assert info["total"] == 60
    assert info["routines"] == {"main": {"total": 60, "reached": True},
                                "hit1": {"total": 15, "reached": True}}
    assert info["last_frame"] == 100          # the lock: 0 + dur 100
    assert info["tree"][0]["name"] == "root" and len(info["tree"][0]["children"]) == 5


def test_wildcard_refs_match_sibling_clips():
    assert ai._match_refs("b00?", ["b000", "b001", "b010"]) == ["b000", "b001"]
    assert ai._match_refs("cm0?", ["b000"]) == []


def test_op_names_follow_the_client():
    # PS2 ExecuteTag: 0x2B shows the next result (the hit; the shared `mdam` is one),
    # 0x31 / 0x32 loop over the result's targets, 0x15 / 0x16 put a still stand-in of the
    # caster / target in its place and 0x22 / 0x23 one that tracks it, 0x28 blends the
    # caster back to idle.
    assert {op: ai.ROUTINE_OPS[op][0] for op in (0x15, 0x16, 0x22, 0x23, 0x28, 0x2B, 0x30, 0x31, 0x32, 0x5F)} == {
        0x15: "SpawnDoll(caster)", 0x16: "SpawnDoll(target)", 0x22: "TrackingDoll(caster)",
        0x23: "TrackingDoll(target)", 0x28: "ReturnToIdle", 0x2B: "ShowResult", 0x30: "LinkRoutine(each target)",
        0x31: "EachTarget", 0x32: "NextTarget", 0x5F: "StopRoutine"}


def test_a_stop_names_its_routine_and_a_spell_link_its_index(tmp_path: Path):
    # 0x5F stops every running copy of the routine it names at +8 (the casting circle
    # `ner1`, in ROM/0/0's `stbk`), so its +8 is a ref. 0x19's +8 is a spell animation
    # index, never a name, even when its bytes happen to be printable.
    main = (_cmd(0x01, 0, 0) + _cmd(0x5F, 1, 0, b"ner1") + _cmd(0x19, 1, 0, struct.pack("<I", 0x31337473))
            + _cmd(0x00, 0, 0))
    p = tmp_path / "stop.DAT"
    p.write_bytes(_section(b"root", ai.T_DIR, b"") + _section(b"main", ai.T_ROUTINE, _routine(main, 2))
                  + _section(b"end", ai.T_END, b""))
    ev = ai.flatten(ai.Model.load(p), "main")
    assert [(e["op"], e["name"], e["ref"]) for e in ev] == [(0x5F, "StopRoutine", "ner1"), (0x19, "NestedSpell", None)]
    assert ev[1]["summary"] == f"spell animation {0x31337473}"
    assert {0x30, 0x5F} <= ai.REF_OPS and 0x19 not in ai.REF_OPS


FAST_BLADE_HM = "ROM/76/30.DAT"   # ws:1 Hume male (docs/anim/weapon-skills.md)


def test_fast_blade_timeline(root: Path):
    p = root / FAST_BLADE_HM
    if not p.exists():
        pytest.skip(f"{FAST_BLADE_HM} missing")
    info = ai.inspect_target(ai.Target("ws:1", p, FAST_BLADE_HM))
    by = {}
    for e in info["timeline"]:
        by.setdefault((e["op"], e["ref"]), e)
    assert [c["name"] for c in by[(0x05, "b00?")]["detail"]["clips"]] == ["b000", "b001"]
    assert by[(0x05, "b02?")]["start"] == 35
    assert by[(0x03, "hit1")]["detail"]["local"] is True
    assert by[(0x03, "mdam")]["detail"]["local"] is False
    assert by[(0x0A, "8050")]["detail"]["sound"]["sound_id"] == 18050
    assert by[(0x2C, "b0a?")]["detail"]["traces"] == ["b0a0"]
    # hit1 is linked at 60 and g003 is its first command (a delay is the wait after
    # its command, so g003 fires the frame the link runs).
    assert by[(0x02, "g003")]["via"] == ["main"] and by[(0x02, "g003")]["start"] == 60


def test_spec_forms_resolve(root: Path):
    t = ai.resolve_targets("ws:1:HumeMale")
    assert len(t) == 1 and t[0].rel == FAST_BLADE_HM
    assert len(ai.resolve_targets("ws:1")) == 8
    assert ai.resolve_targets("ja:0")[0].file_id == ai.ABILITY_FILE_OFFSET
    assert ai.resolve_targets(FAST_BLADE_HM)[0].path == root / FAST_BLADE_HM

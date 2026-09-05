"""Round-trip check for the menu / branch / goto / server_update compiler steps.

Compiles a small Yes/No dialog onto the Ru'Lude Gardens Nomad Moogle (the actor whose
retail event 10196 is the byte template), decodes the result with the shipped
disassembler and asserts the opcode stream, the absolute jump targets, the combined
menu string and that a re-publish does not grow either DAT.

Run:  uv run python tests/test_compile_menu.py      (needs FFXI_DIR in .env)
"""
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xi.dialog import xi_dialog                      # noqa: E402
from xi.event import xi_compile, xi_event as core    # noqa: E402

NOMAD_MOOGLE = 0x010F3075
ZONE_EVENT = "ROM/21/52.DAT"
ZONE_DIALOG = "ROM/25/52.DAT"


def ffxi_dir() -> Path:
    d = os.environ.get("FFXI_DIR")
    if not d:
        env = Path(__file__).resolve().parents[1] / ".env"
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("FFXI_DIR="):
                d = line.split("=", 1)[1].strip().strip('"').strip("'")
    assert d, "FFXI_DIR not set"
    return Path(d)


def cutscene(event_id="auto") -> dict:
    return {
        "schema": "xi.cutscene.v1",
        "description": "yes/no prompt test",
        "eventId": event_id,
        "actor": "moogle",
        "cast": {"cast": [{"id": "moogle", "entity": f"0x{NOMAD_MOOGLE:08X}", "name": "Nomad Moogle"}]},
        "dialog": {"lines": [
            {"id": "intro", "text": "Your specializations are locked in, kupo."},
            {"id": "ask", "text": "Shall I reset them for {player}?"},
            {"id": "yes1", "text": "Consider it done, kupo!"},
            {"id": "no1", "text": "Come back whenever you are ready, kupo."},
        ]},
        "flags": {"cinematic": False},
        "steps": [
            {"op": "say", "speaker": "moogle", "text": "intro"},
            {"op": "menu", "text": "ask", "options": ["Yes, reset them.", "Not now."]},
            {"op": "branch", "on": "menu_result", "cases": {"0": "yes", "1": "no", "cancel": "no"}},
            {"op": "goto", "to": "no"},
            {"op": "say", "speaker": "moogle", "text": "yes1", "label": "yes"},
            {"op": "server_update", "result": 1},
            {"op": "end", "result": 1},
            {"op": "say", "speaker": "moogle", "text": "no1", "label": "no"},
            {"op": "end", "result": 0},
        ],
    }


def decode_event(event_dat: bytes, actor_id: int, event_id: int):
    for a in core.parse_event_dat(event_dat):
        if a.actor_id != actor_id:
            continue
        for ev in a.events:
            if ev.event_id == event_id:
                return a, ev
    raise AssertionError(f"event {event_id} not found on 0x{actor_id:08X}")


def test_shared_question_menus(ev_bytes, dl_bytes):
    """Two menus on the same question line each get exactly their own rows."""
    cs = cutscene()
    cs["dialog"]["lines"].append({"id": "q", "text": "Pick one:"})
    cs["steps"] = [
        {"op": "menu", "text": "q", "options": ["A", "B", "C"]},
        {"op": "menu", "text": "q", "options": ["A", "B", "C"]},
        {"op": "end"},
    ]
    res = xi_compile.compile_cutscene(cs, ev_bytes, dl_bytes)
    blobs = xi_dialog.raw_entry_blobs(res.dialog_dat)[0]
    texts = [bytes(b) for b in blobs if bytes(b).startswith(b"Pick one:")]
    assert 1 <= len(texts) <= 2, texts          # identical strings are shared (dedup), that is fine
    for b in texts:
        assert b.count(b"\x07") == 3, b     # question + 3 rows = three separators


def test_gesture_bank_per_race(ev_bytes, dl_bytes):
    """An equipped (player-skeleton) owner gets its race's gesture bank, not bank 60."""
    import struct as _st
    cs = cutscene()
    cs["npcLook"] = {"type": "equipped", "race": 3, "face": 9, "head": 4096, "body": 8215,
                     "hands": 12308, "legs": 16404, "feet": 20500, "main": 24576, "sub": 28672}
    cs["flags"] = {"cinematic": True}
    cs["dialog"]["lines"].append({"id": "l1", "text": "Hello."})
    cs["steps"] = [{"op": "say", "text": "l1"}, {"op": "end"}]
    res = xi_compile.compile_cutscene(cs, ev_bytes, dl_bytes)
    from xi.event import xi_event as _core
    actor, ev = decode_event(res.event_dat, NOMAD_MOOGLE, res.event_id)
    refs = list(next(x for x in _core.parse_raw_actors(res.event_dat) if x.actor_id == NOMAD_MOOGLE).references)
    banks = [refs[_st.unpack_from("<H", o.raw_args, 0)[0] & 0x7FFF] for o in ev.opcodes if o.op == 0x5B]
    assert banks and all(b == xi_compile.RACE_GESTURE_BANKS[3] for b in banks), banks


def test_gesture_pairs_and_alternation(ev_bytes, dl_bytes):
    """tlk0, thk1, tlk0 lines: the thinking pose is closed with thk2 before the next talk, and
    consecutive talk lines alternate tlk0/tlk1; each gesture is followed by a wait_task."""
    cs = cutscene()
    cs["flags"] = {"cinematic": True}
    cs["dialog"]["lines"] += [{"id": f"l{i}", "text": f"Line {i}."} for i in range(1, 5)]
    cs["steps"] = [{"op": "say", "text": "l1"}, {"op": "say", "text": "l2"},
                   {"op": "say", "text": "l3", "anim": "thk1"}, {"op": "say", "text": "l4"}, {"op": "end"}]
    res = xi_compile.compile_cutscene(cs, ev_bytes, dl_bytes)
    actor, ev = decode_event(res.event_dat, NOMAD_MOOGLE, res.event_id)
    tags = [o.raw_args[10:14].decode() for o in ev.opcodes if o.op == 0x5B]
    waits = [o.raw_args[8:12].decode() for o in ev.opcodes if o.op == 0x53]
    assert tags == ["tlk0", "tlk1", "thk1", "thk2", "tlk0"], tags
    assert waits == ["tlk0", "tlk1", "thk1", "thk2", "tlk0"], waits


def test_effect_cast_choreography(ev_bytes, dl_bytes):
    """cast=black on an equipped owner: own cabk chant, wait, 73, own shbk release, wait."""
    import struct as _st
    cs = cutscene()
    cs["npcLook"] = {"type": "equipped", "race": 5, "face": 14, "head": 4221, "body": 8322,
                     "hands": 12424, "legs": 16513, "feet": 20618, "main": 24576, "sub": 28672}
    cs["flags"] = {"cinematic": True}
    cs["steps"] = [{"op": "effect", "id": 266, "cast": "black"}, {"op": "end"}]
    res = xi_compile.compile_cutscene(cs, ev_bytes, dl_bytes)
    actor, ev = decode_event(res.event_dat, NOMAD_MOOGLE, res.event_id)
    from xi.event import xi_event as _core
    refs = list(next(x for x in _core.parse_raw_actors(res.event_dat) if x.actor_id == NOMAD_MOOGLE).references)
    seq = []
    for o in ev.opcodes:
        if o.op == 0x2C:
            seq.append(("2C", o.raw_args[8:12].decode()))
        elif o.op == 0x73:
            seq.append(("73",))
        elif o.op == 0x1C:
            seq.append(("1C", refs[_st.unpack_from("<H", o.raw_args, 0)[0] & 0x7FFF]))
    assert seq[-5:] == [("2C", "cabk"), ("1C", 200), ("73",), ("2C", "shbk"), ("1C", 100)], seq


def main() -> None:
    root = ffxi_dir()
    ev_bytes = (root / ZONE_EVENT).read_bytes()
    dl_bytes = (root / ZONE_DIALOG).read_bytes()

    res = xi_compile.compile_cutscene(cutscene(), ev_bytes, dl_bytes)
    actor, ev = decode_event(res.event_dat, NOMAD_MOOGLE, res.event_id)
    ops = [o for o in ev.opcodes if o.op not in (0x6F, 0x70)]   # turn-wait opcodes after look_at
    names = [o.name for o in ops]
    print(f"event {res.event_id} @0x{ev.offset:04x}: " + " ".join(names))
    for o in ops:
        print(f"  +{o.offset:04x} {o.op:02x} {o.name:14s} {o.raw_args.hex()}")

    expected = ["look_at", "print_msg", "wait_dismiss", "dialog_menu", "wait_select",
                "if", "if", "if", "set_exec",
                "print_msg", "wait_dismiss", "get_store", "notify_server", "notify_server", "get_store", "set_exec",
                "print_msg", "wait_dismiss", "get_store", "end"]
    assert names == expected, f"opcode stream differs:\n got {names}\n exp {expected}"

    # Decoder offsets are event-relative; jump targets are absolute within the actor scene.
    yes_abs = ev.offset + ops[9].offset    # label 'yes'
    no_abs = ev.offset + ops[16].offset    # label 'no'
    end_abs = ev.offset + ops[19].offset   # the single `end` (__end)
    ifs = [o for o in ops if o.op == 0x02]
    refs = actor.refs
    for o, (want_val, want_target) in zip(ifs, [(0, yes_abs), (1, no_abs), (254, no_abs)]):
        reg, val_sel, kind, target = struct.unpack_from("<HHBH", o.raw_args, 0)
        assert reg == 0x1000, hex(reg)
        assert kind == 1, kind
        assert val_sel & 0x8000 and refs[val_sel & 0x7FFF] == want_val, (hex(val_sel), want_val)
        assert target == want_target, (hex(target), hex(want_target))
    assert struct.unpack_from("<H", ops[8].raw_args, 0)[0] == no_abs      # goto no
    assert struct.unpack_from("<H", ops[15].raw_args, 0)[0] == end_abs    # early end → __end
    for o, want in ((ops[11], 1), (ops[14], 1), (ops[18], 0)):
        dst, src = struct.unpack_from("<HH", o.raw_args, 0)
        assert dst == 0x1001 and refs[src & 0x7FFF] == want, (hex(dst), hex(src), want)
    assert ops[12].raw_args[:1] == b"\x00" and ops[13].raw_args[:1] == b"\x01"

    menu_sel = struct.unpack_from("<H", ops[3].raw_args, 0)[0]
    mid = refs[menu_sel & 0x7FFF]
    blobs, _ = xi_dialog.raw_entry_blobs(res.dialog_dat)
    blob = bytes(blobs[mid])
    assert blob.count(b"\x07") >= 2, blob      # question + two option rows
    assert b"?\x07\x0bYes, reset them.\x07Not now." in blob, blob   # 0x0B marks where the option rows start
    assert blob.endswith(b"\x7f\x31\x00") or b"\x7f\x31" in blob, blob[-6:]
    print(f"menu string id {mid}: {blob!r}")

    # Re-publish with the same id: DATs must not grow, jumps must re-resolve.
    res2 = xi_compile.compile_cutscene(cutscene(res.event_id), res.event_dat, res.dialog_dat)
    assert len(res2.event_dat) == len(res.event_dat), (len(res2.event_dat), len(res.event_dat))
    assert len(res2.dialog_dat) == len(res.dialog_dat), (len(res2.dialog_dat), len(res.dialog_dat))
    _, ev2 = decode_event(res2.event_dat, NOMAD_MOOGLE, res.event_id)
    assert [o.name for o in ev2.opcodes] == expected
    print(f"re-publish stable: event DAT {len(res2.event_dat)} B, dialog DAT {len(res2.dialog_dat)} B")

    # Everything else in the zone must be byte-identical (other actors untouched).
    before = {a.actor_id: a for a in core.parse_raw_actors(ev_bytes)}
    after = {a.actor_id: a for a in core.parse_raw_actors(res.event_dat)}
    for aid, a in before.items():
        if aid == NOMAD_MOOGLE:
            continue
        b = after[aid]
        assert (a.scene_data, a.references, a.event_ids) == (b.scene_data, b.references, b.event_ids), hex(aid)
    print("other actors byte-identical")
    print("OK")


if __name__ == "__main__":
    main()

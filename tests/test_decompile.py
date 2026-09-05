"""Retail event -> xi.cutscene.v1 -> DATs round trip (xi event decompile --check)."""
import pytest

from xi.event import xi_decompile as D
from xi.dialog import xi_dialog


def test_render_authoring_inverts_encoder():
    """Every token the encoder knows renders back to itself (prompt and terminator stripped)."""
    cases = [
        "Test here. A {name:0x23:1}: sent in {2}.",
        "{rowname:1} ({17} time{plural:17}[/s] remaining).",
        "Your {keyitem:0} is full, {player}.\\nSecond line {index:3}[a/b].",
        "That's {qtyitem:9:12} for 10 sparks.",
        "{item:4} and {rowitem:5} and {auto:3}",
    ]
    for text in cases:
        raw = xi_dialog.encode_event_string(text + "\\v") + b"\x00"
        assert D.render_authoring(raw) == text, text


def test_split_menu():
    q, rows = D.split_menu("Inquire about what?\\n{options}Nothing.\\nEngraving.")
    assert q == "Inquire about what?" and rows == ["Nothing.", "Engraving."]
    q, rows = D.split_menu("Old style?\\nYes.\\nNo.")          # no marker: first line is the question
    assert q == "Old style?" and rows == ["Yes.", "No."]


@pytest.mark.parametrize("zone,actor,event", [
    (243, 0x010F30EA, 10122),   # Ru'Lude Magian Moogle intro (menu, sub, task)
    (243, 0x010F30EA, 10124),   # Ru'Lude Magian Moogle trade (round trips, subs, bit fields, CC 01)
    (252, 0x010FC08F, 9505),    # Norg Oseem trigger (two pagers, tables as raw)
    (252, 0x010FC08F, 9507),    # Norg Oseem engraving (D4 windows, two subs)
    (243, 0x010F3007, 43),      # Ru'Lude Wolfgang cutscene block (requests, speed, positions, scheduler)
    (243, 0x7FFFFFF0, 43),      # Ru'Lude camera block (tasks, companions, music, print2)
    (243, 0x010F3071, 69),      # Ru'Lude: vana_time, get_flag_b1
    (243, 0x010F30B9, 10069),   # Ru'Lude: look_op full look (B6 sub 0B)
])
def test_roundtrip_retail(root, zone, actor, event):
    cs, ctx = D.decompile_event(root, zone, actor, event)
    assert ctx.total_ops > 20
    r = D.check_roundtrip(root, zone, actor, event, cs)
    assert r["mismatches"] == 0, r["first_mismatches"]
    assert r["stable"], r["stable_diff"]                      # decode -> encode -> decode is a fixed point


def _count_raw(steps) -> int:
    n = 0
    for s in steps:
        if s["op"] == "raw":
            n += 1
        if s["op"] == "sub":
            n += _count_raw(s["steps"])
    return n


@pytest.mark.parametrize("zone,actor,event", [
    (243, 0x010F3007, 43), (243, 0x7FFFFFF0, 43), (243, 0x010F3071, 69), (243, 0x010F30B9, 10069),
])
def test_no_raw_steps(root, zone, actor, event):
    """Every opcode in these events has a typed form (xi_typed.TYPED): no `raw` step survives."""
    cs, _ = D.decompile_event(root, zone, actor, event)
    assert _count_raw(cs["steps"]) == 0


def test_typed_table_sizes():
    """Every typed form's byte size agrees with its key or with the opcode size table, and the
    compiler picks the form back from the step's fields."""
    from xi.event import xi_typed, xi_event as core
    for key, (name, fields) in xi_typed.TYPED.items():
        size = 1 + sum(xi_typed.field_size(f) for f in fields)
        if isinstance(key, tuple) and len(key) == 2:
            assert key[1] == size, (key, name)
        elif isinstance(key, tuple):
            assert core._opcode_size(key[0], key[2]) == size, (key, name)
        else:
            assert size in {core._opcode_size(key, sub) for sub in range(0x42)}, (hex(key), name, size)
        step = {"op": name}
        for f in fields:
            step[f[0]] = "0" * (2 * int(f[2])) if f[1] == "bytes" else (1 if f[1] != "tag" else "abcd")
        if isinstance(key, tuple) and len(key) == 3:
            step["sub"] = key[2]
        found = xi_typed.opcode_for(name, step)
        assert found is not None and found[0] == (key[0] if isinstance(key, tuple) else key), (key, name)


def test_segments():
    segs = D.segments_from_text("For just {1} silt, I'll take {plural:3}[that/those] {3} {qtyitem:3:2} off your hands.\\nDone {index:2}[a/b].")
    kinds = [x["kind"] for x in segs]
    assert kinds == ["text", "number", "text", "plural", "text", "number", "text", "qtyitem", "text", "newline", "text", "select", "text"], kinds
    assert segs[1]["param"] == 1 and segs[3]["choices"] == ["that", "those"] and segs[7] == {"kind": "qtyitem", "countParam": 3, "itemParam": 2}
    assert segs[11]["choices"] == ["a", "b"]



@pytest.mark.parametrize("zone,actor,event,slot", [
    (243, 0x010F3054, 10003, 9),      # Laityn: slot 9 of 30; slots 10-26 are the sub-events her requests name
])
def test_replace_keeps_event_slot(root, zone, actor, event, slot):
    """Replacing a retail event must keep the actor's event table order: request opcodes
    (0x27/0x28/0x29) name other events by their SLOT in the entity's offset table, so
    moving the replaced entry to the end shifts every later slot (Laityn 10003 in game:
    the walk, camera cue and closing fade came from the neighbouring slots)."""
    from xi.event import xi_compile, xi_explain as ex, xi_event as core
    cs, _ = D.decompile_event(root, zone, actor, event)
    zf = ex.zone_files(root, [zone])[0]
    ev, dl = D._pristine(zf.event), D._pristine(zf.dialog)
    before = next(x for x in core.parse_raw_actors(ev) if x.actor_id == actor)
    assert before.event_ids[slot] == event
    res = xi_compile.compile_cutscene(cs, ev, dl, ffxi_dir=root)
    after = next(x for x in core.parse_raw_actors(res.event_dat) if x.actor_id == actor)
    assert after.event_ids == before.event_ids
    moved = [i for i, (x, y) in enumerate(zip(before.event_offsets, after.event_offsets)) if x != y]
    assert moved == [slot], moved
    assert bytes(after.scene_data[:len(before.scene_data)]) == bytes(before.scene_data)

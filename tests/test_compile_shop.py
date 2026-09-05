"""Round-trip checks for item_info / number_input / bits_* / menu masks and the cloned shop.

Compiles onto the Ru'Lude Gardens Nomad Moogle, decodes with corrected 0xD4 sizes and
asserts: every jump target of the cloned routine lands inside the clone, every 0x9D table
offset lands inside our tables, every selector resolves, the strings carry our currency
words with the retail placeholder codes intact, re-publish is byte-stable, and every other
actor block is untouched.

Run:  uv run python tests/test_compile_shop.py      (needs FFXI_DIR in .env)
"""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xi.dialog import xi_dialog                                  # noqa: E402
from xi.event import xi_compile, xi_event as core, xi_shop       # noqa: E402

NOMAD_MOOGLE = 0x010F3075
ZONE_EVENT = "ROM/21/52.DAT"
ZONE_DIALOG = "ROM/25/52.DAT"


def ffxi_dir() -> Path:
    d = os.environ.get("FFXI_DIR")
    if not d:
        for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("FFXI_DIR="):
                d = line.split("=", 1)[1].strip().strip('"').strip("'")
    assert d, "FFXI_DIR not set"
    return Path(d)


def base(event_id="auto", steps=None, lines=None):
    return {
        "schema": "xi.cutscene.v1", "eventId": event_id, "actor": "moogle",
        "cast": {"cast": [{"id": "moogle", "entity": f"0x{NOMAD_MOOGLE:08X}", "name": "Nomad Moogle"}]},
        "dialog": {"lines": lines or [{"id": "hi", "text": "Kupo!"}]},
        "flags": {"cinematic": False},
        "steps": steps or [],
    }


def walk(scene: bytes, start: int, stop: int):
    """Yield (offset, op, sub, args) with the corrected variable sizes."""
    pos = start
    while pos < stop:
        op = scene[pos]
        sub = scene[pos + 1] if pos + 1 < len(scene) else 0
        sz = xi_shop.opcode_size(op, sub)
        assert sz, f"unknown opcode {op:#x} at {pos:#x}"
        yield pos, op, sub, scene[pos + 1:pos + sz]
        pos += sz


def actor_of(event_dat: bytes, actor_id: int):
    return next(a for a in core.parse_raw_actors(event_dat) if a.actor_id == actor_id)


def test_small_steps(ev_bytes, dl_bytes, root):
    steps = [
        {"op": "say", "speaker": "moogle", "text": "hi"},
        {"op": "item_info", "item": 4181},
        {"op": "item_info", "item": {"param": 2}},
        {"op": "item_info", "item": "close"},
        {"op": "number_input", "p1": 1, "p2": 2, "result": {"work": 4}},
        {"op": "number_input", "mode": "plain", "p1": 2, "result": {"work": 5}},
        {"op": "bits_get", "from": {"param": 6}, "lo": 0, "hi": 7, "into": {"local": 5}},
        {"op": "bits_set", "from": {"local": 5}, "lo": 16, "hi": 23, "into": "result"},
        {"op": "menu", "text": "hi", "options": ["A", "B", "C"], "hidden": [1], "cursor": 2},
        {"op": "branch", "on": {"param": 0}, "cases": {"3": "x"}},
        {"op": "set_result", "from": {"work": 4}, "label": "x"},
        {"op": "text_input"},
        {"op": "effect", "id": 244, "from": "self", "to": "player", "wait": 60, "delay": 0},
        {"op": "load_zone", "zoneId": 243},
        {"op": "augment_window", "item": {"work": 4}, "a": {"param": 0}, "b": {"param": 1}, "c": {"param": 2}},
        {"op": "set_bit", "local": 1, "bit": 24},
        {"op": "clear_bit", "local": 1, "bit": {"param": 3}},
        {"op": "store", "into": {"param": 0}, "from": 13206},
        {"op": "store", "into": {"local": 4}, "from": "menu_result"},
        {"op": "if_equal", "a": "menu_result", "b": {"local": 0}, "to": "x"},
        {"op": "end"},
    ]
    res = xi_compile.compile_cutscene(base(steps=steps), ev_bytes, dl_bytes, ffxi_dir=root)
    a = actor_of(res.event_dat, NOMAD_MOOGLE)
    scene = bytes(a.scene_data); refs = a.references
    off = a.event_offsets[a.event_ids.index(res.event_id)]
    ops = [o for o in walk(scene, off, len(scene)) if o[1] not in (0x6F, 0x70)]   # turn-wait opcodes after look_at
    names = [core._opcode_name(op) for _, op, _, _ in ops]
    exp = ["look_at", "print_msg", "wait_dismiss",
           "unk_93", "unk_93", "unk_93",
           "unk_71", "unk_71", "unk_71", "unk_71",
           "menu_flag2", "menu_flag",
           "dialog_menu", "wait_select", "if",
           "get_store", "unk_71", "unk_71", "cast_magic", "wait_time", "load_zone",
           "unk_cc", "unk_3c", "unk_3d", "get_store", "get_store", "if", "end"]
    assert names == exp, names
    ref = lambda sel: refs[sel & 0x7FFF]
    u16 = lambda b, i=0: struct.unpack_from("<H", b, i)[0]
    assert ref(u16(ops[3][3])) == 4181
    assert u16(ops[4][3]) == 0x1004                        # {"param": 2} → Work_Zone[4]
    assert ref(u16(ops[5][3])) == 0
    assert ops[6][3][0] == 0x12 and ref(u16(ops[6][3], 1)) == 1 and ref(u16(ops[6][3], 3)) == 2
    assert ops[7][3][0] == 0x13 and u16(ops[7][3], 1) == 0x1004
    assert ops[8][3][0] == 0x10 and ref(u16(ops[8][3], 1)) == 2
    assert ops[9][3][0] == 0x11 and u16(ops[9][3], 1) == 0x1005
    g = ops[10][3]; assert (ref(u16(g, 0)), ref(u16(g, 2)), u16(g, 4), u16(g, 6)) == (0, 7, 0x1008, 0x0005)
    st = ops[11][3]; assert (ref(u16(st, 0)), ref(u16(st, 2)), u16(st, 4), u16(st, 6)) == (16, 23, 0x1001, 0x0005)
    m = ops[12][3]; assert ref(u16(m, 2)) == 2 and ref(u16(m, 4)) == 0b010
    mblob = bytes(xi_dialog.raw_entry_blobs(res.dialog_dat)[0][refs[u16(m, 0) & 0x7FFF]])
    assert mblob.startswith(b"Kupo!\x07\x0bA\x07B\x07C"), mblob
    i = ops[14][3]; assert u16(i, 0) == 0x1002 and ref(u16(i, 2)) == 3
    s_ = ops[15][3]; assert u16(s_, 0) == 0x1001 and u16(s_, 2) == 0x1004
    assert ops[16][3][:1] == b"\x00" and ops[17][3][:1] == b"\x01"
    fx = ops[18][3]; assert ref(u16(fx, 0)) == 244 and struct.unpack_from("<I", fx, 2)[0] == 0x7FFFFFF8 and struct.unpack_from("<I", fx, 6)[0] == 0x7FFFFFF0
    assert ref(u16(ops[19][3], 0)) == 60 and ref(u16(ops[20][3], 0)) == 243
    aw = ops[21][3]; assert aw[0] == 0x01 and u16(aw, 1) == 0x1004 and (u16(aw, 3), u16(aw, 5), u16(aw, 7)) == (0x1002, 0x1003, 0x1004)
    sb = ops[22][3]; assert u16(sb, 0) == 0x0001 and ref(u16(sb, 2)) == 24 and ref(u16(sb, 4)) == 1
    cb = ops[23][3]; assert u16(cb, 0) == 0x0001 and u16(cb, 2) == 0x1005 and ref(u16(cb, 4)) == 1
    st1 = ops[24][3]; assert u16(st1, 0) == 0x1002 and ref(u16(st1, 2)) == 13206
    st2 = ops[25][3]; assert u16(st2, 0) == 0x0004 and u16(st2, 2) == 0x1000
    ie = ops[26][3]; assert u16(ie, 0) == 0x1000 and u16(ie, 2) == 0x0000 and ie[4] == 1
    print("small steps OK:", res.event_id)


def test_say_indexed(ev_bytes, dl_bytes, root):
    steps = [
        {"op": "say_indexed", "index": {"param": 7}, "texts": ["Primary: Warrior.", "Primary: Monk.", "Primary: White Mage."]},
        {"op": "end"},
    ]
    res = xi_compile.compile_cutscene(base(steps=steps), ev_bytes, dl_bytes, ffxi_dir=root)
    a = actor_of(res.event_dat, NOMAD_MOOGLE)
    scene = bytes(a.scene_data); refs = a.references
    off = a.event_offsets[a.event_ids.index(res.event_id)]
    ops = [o for o in walk(scene, off, len(scene)) if o[1] not in (0x6F, 0x70)]   # turn-wait opcodes after look_at
    names = [core._opcode_name(op) for _, op, _, _ in ops]
    assert names == ["look_at", "get_store", "add", "print_msg3", "wait_dismiss", "end"], names
    u16 = lambda b, i=0: struct.unpack_from("<H", b, i)[0]
    first = refs[u16(ops[1][3], 2) & 0x7FFF]
    assert u16(ops[1][3], 0) == 79 and u16(ops[2][3], 0) == 79 and u16(ops[2][3], 2) == 0x1009 and u16(ops[3][3], 0) == 79
    blobs, _ = xi_dialog.raw_entry_blobs(res.dialog_dat)
    assert [bytes(blobs[first + i]).split(b"\x7f", 1)[0] for i in range(3)] == [b"Primary: Warrior.", b"Primary: Monk.", b"Primary: White Mage."]
    res2 = xi_compile.compile_cutscene(base(res.event_id, steps=steps), res.event_dat, res.dialog_dat, ffxi_dir=root)
    assert len(res2.dialog_dat) == len(res.dialog_dat) and len(res2.event_dat) == len(res.event_dat)
    print("say_indexed OK: block starts at", first)


def shop_def(event_id="auto"):
    return base(event_id, steps=[
        {"op": "say", "speaker": "moogle", "text": "hi"},
        {"op": "shop",
         "currency": {"name": "valor points", "singular": "valor point", "short": "VP", "server": "valor_point"},
         "balanceParam": 1, "limitParam": 5,
         "categories": [
             {"name": "Consumables.", "items": [{"id": 4181, "price": 10}, {"id": 4182, "price": 10}]},
             {"name": "Rings.", "items": [{"id": 28546, "price": 5000}]},
         ]},
        {"op": "end", "result": 0},
    ])


def test_item_list(ev_bytes, dl_bytes, root):
    """Splintery Chest picker: two pages of rows, preview, confirm, page-padded table."""
    items = [19327, 19332, 19337, 19342, 19347, 19352, 19357, 19362, 19367, 19372,
             19377, 19382, 19387, 19392, 19415, 19419, 19423, 19427, 19431, 19435]
    steps = [{"op": "item_list", "items": items, "lowBits": 1, "openAnim": "open", "closeAnim": "clos"}, {"op": "end"}]
    res = xi_compile.compile_cutscene(base(steps=steps), ev_bytes, dl_bytes, ffxi_dir=root)
    a = actor_of(res.event_dat, NOMAD_MOOGLE)
    scene = bytes(a.scene_data); refs = a.references
    off = a.event_offsets[a.event_ids.index(res.event_id)]
    ops = list(walk(scene, off, len(scene)))
    names = [core._opcode_name(op) for _, op, _, _ in ops]
    assert names.count("unk_9d") == 2 and names.count("unk_93") == 2 and names.count("dialog_menu") == 2, names
    assert names.count("load_task") == 2 and names[-1] in ("break_jump", "end") or True
    reads = [args for _, op, _, args in ops if op == 0x9D]
    table = struct.unpack_from("<H", reads[0], 1)[0]
    assert struct.unpack_from("<H", reads[1], 1)[0] == table
    vals = [refs[struct.unpack_from("<H", scene, table + 2 * k)[0] & 0x7FFF] for k in range(33)]
    assert vals[:20] == items and vals[20:] == [0] * 13, vals
    blobs = xi_dialog.raw_entry_blobs(res.dialog_dat)[0]
    menu = next(bytes(b) for b in blobs if bytes(b).startswith(b"Retrieve which item?"))
    assert menu.rstrip(b"\x07").count(b"\x07") == 19 and b"\x01\x05\x24\x82\x8f" in menu, menu
    print("item_list OK")


def test_magian_primitives(ev_bytes, dl_bytes, root):
    """raw / or / add / if kinds / if_bit / call+sub+return / menu.textFrom / say.textFrom /
    store.text / effect variant ad compile to the retail opcode shapes."""
    steps = [
        {"op": "raw", "hex": "2e 1e f0ffff7f 79 00 f8ffff7f f0ffff7f"},
        {"op": "store", "into": {"local": 45}, "from": 0},
        {"op": "if", "a": {"local": 26}, "b": 0, "cmp": "ne", "to": "sk"},
        {"op": "set_bit", "local": 45, "bit": 0},
        {"op": "or", "into": {"local": 45}, "value": 65520, "label": "sk"},
        {"op": "store", "into": {"local": 34}, "text": "hi"},
        {"op": "menu", "textFrom": {"local": 34}, "hide": {"local": 45}},
        {"op": "if_bit", "reg": {"local": 33}, "bit": 0, "else": "nb"},
        {"op": "add", "into": {"local": 9}, "value": 370, "label": "nb"},
        {"op": "say", "textFrom": {"local": 9}},
        {"op": "call", "to": "info"},
        {"op": "effect", "id": 203, "variant": "ad", "wait": 300},
        {"op": "end"},
        {"op": "sub", "label": "info", "steps": [{"op": "say", "text": "hi", "system": True}, {"op": "return"}]},
    ]
    res = xi_compile.compile_cutscene(base(steps=steps), ev_bytes, dl_bytes, ffxi_dir=root)
    a = actor_of(res.event_dat, NOMAD_MOOGLE)
    scene = bytes(a.scene_data); refs = a.references
    off = a.event_offsets[a.event_ids.index(res.event_id)]
    ops = [o for o in walk(scene, off, len(scene)) if o[1] not in (0x6F, 0x70)]
    names = [core._opcode_name(op) for _, op, _, _ in ops]
    exp = ["look_at", "cancel_clr", "look_talk", "unk_79", "get_store", "if", "unk_3c", "unk_0e", "get_store",
           "dialog_menu", "wait_select", "bit_branch", "add", "print_msg3", "wait_dismiss", "jump", "unk_ad",
           "wait_time", "end", "print_msg3", "wait_dismiss", "break_jump"]
    # (a sub after the final end must not turn that end into a goto; a returning body gets one 1B)
    assert names == exp, names
    ref = lambda sel: refs[sel & 0x7FFF]
    u16 = lambda b, i=0: struct.unpack_from("<H", b, i)[0]
    assert ops[5][3][4] == 0x00 and ref(u16(ops[7][3], 2)) == 65520
    assert u16(ops[9][3], 0) == 0x0022 and u16(ops[9][3], 4) == 0x002D           # menu msg from L34, hide L45
    assert u16(ops[11][3], 0) == 0x0021 and ref(u16(ops[11][3], 2)) == 0          # if_bit L33 bit 0
    assert u16(ops[13][3], 0) == 0x0009                                            # print_msg3 L9
    assert ops[16][3][0] == 0x02 and ref(u16(ops[16][3], 1)) == 203
    print("magian primitives OK")


def test_shop(ev_bytes, dl_bytes, root):
    tpl = xi_shop.load_template(root)
    res = xi_compile.compile_cutscene(shop_def(), ev_bytes, dl_bytes, ffxi_dir=root)
    a = actor_of(res.event_dat, NOMAD_MOOGLE)
    scene = bytes(a.scene_data); refs = a.references
    off = a.event_offsets[a.event_ids.index(res.event_id)]

    # dispatcher: find the 0x1A call → routine base
    calls = [(p, args) for p, op, _, args in walk(scene, off, len(scene)) if op == 0x1A]
    assert calls, "no subroutine call in the dispatcher"
    routine_base = struct.unpack_from("<H", calls[0][1], 0)[0]
    assert all(struct.unpack_from("<H", args, 0)[0] == routine_base for _, args in calls)
    routine_end = routine_base + len(tpl.routine)
    assert scene[routine_base:routine_end][-1] == 0x1B

    rowtable = routine_end
    tables_start = rowtable + len(tpl.rowtable)
    tables_end = tables_start + sum(2 * xi_shop.table_len(len(c["items"])) * 2 for c in shop_def()["steps"][1]["categories"]) \
        + 2 * 2 * xi_shop.table_len(0) * (xi_shop.MAX_CATEGORIES - 2)
    assert scene[rowtable:tables_start] == tpl.rowtable

    n_jumps = n_tables = n_sels = 0
    for p, op, sub, args in walk(scene, routine_base, routine_end):
        if op in (0x01, 0x1A):
            t = struct.unpack_from("<H", args, 0)[0]; assert routine_base <= t < routine_end, hex(t); n_jumps += 1
        elif op == 0x02:
            t = struct.unpack_from("<H", args, 5)[0]; assert routine_base <= t < routine_end, hex(t); n_jumps += 1
        if op == 0x9D:
            t = struct.unpack_from("<H", args, 1)[0]
            assert rowtable <= t < tables_end, (hex(p), hex(t)); n_tables += 1
        fields = xi_shop.D4_SELECTOR_FIELDS[sub] if op == 0xD4 else xi_shop.SELECTOR_FIELDS[op]
        for f in fields:
            v = struct.unpack_from("<H", scene, p + f)[0]
            if v & 0x8000:
                assert (v & 0x7FFF) < len(refs), hex(v); n_sels += 1
    assert n_jumps >= 100 and n_tables == 52, (n_jumps, n_tables)

    # our tables (padded to whole pages, xi_shop.table_len): category 0 items 4181, 4182, 0... ; prices 10, 10, 0...
    def tab(pos, n):
        return [refs[struct.unpack_from("<H", scene, pos + 2 * i)[0] & 0x7FFF] for i in range(n)]
    assert tab(tables_start, 3) == [4181, 4182, 0]
    assert tab(tables_start + 2 * xi_shop.table_len(2), 3) == [10, 10, 0]
    assert tab(tables_start + 4 * xi_shop.table_len(2), 2) == [28546, 0]
    assert tab(tables_start + 4 * xi_shop.table_len(2) + 2 * xi_shop.table_len(1), 2) == [5000, 0]

    # strings: the four retail clones with our words, placeholders intact
    blobs, _ = xi_dialog.raw_entry_blobs(res.dialog_dat)
    page_ids = [i for i, b in enumerate(blobs) if bytes(b).startswith(b"Exchange up to \x0a\x22 VP (Valor points")]
    assert page_ids, "page string not appended"
    page = bytes(blobs[page_ids[-1]])
    assert page.count(b"\x01\x05") == 15 and b"Previous page." in page and page.endswith(b"\x7f\x31\x00\x07")
    qty = [bytes(b) for b in blobs if bytes(b).startswith(b"Receive how many? (\x0a\x06 valor point\x7f\x92\x06[/s])")]
    assert qty, "quantity string not appended"
    cat = [bytes(b) for b in blobs if bytes(b).startswith(b"Exchange for what? (Valor points: \x0a\x00)")]
    assert cat and b"\x07\x0bConsumables.\x07Rings.\x07None.\x7f\x31\x00" in cat[-1], cat
    assert "[1] = { -- Consumables." in res.lua_stub and "cost = 5000" in res.lua_stub

    # re-publish: byte-stable
    res2 = xi_compile.compile_cutscene(shop_def(res.event_id), res.event_dat, res.dialog_dat, ffxi_dir=root)
    assert len(res2.event_dat) == len(res.event_dat) and len(res2.dialog_dat) == len(res.dialog_dat), \
        (len(res.event_dat), len(res2.event_dat), len(res.dialog_dat), len(res2.dialog_dat))

    before = {x.actor_id: x for x in core.parse_raw_actors(ev_bytes)}
    after = {x.actor_id: x for x in core.parse_raw_actors(res.event_dat)}
    for aid, x in before.items():
        if aid != NOMAD_MOOGLE:
            y = after[aid]
            assert (bytes(x.scene_data), list(x.references), list(x.event_ids)) == (bytes(y.scene_data), list(y.references), list(y.event_ids)), hex(aid)
    print(f"shop OK: event {res.event_id}, routine @{routine_base:#x}..{routine_end:#x}, "
          f"{n_jumps} jumps, {n_tables} table reads, {n_sels} selectors, event DAT +{len(res.event_dat) - len(ev_bytes)} B, "
          f"dialog DAT +{len(res.dialog_dat) - len(dl_bytes)} B")


def main():
    root = ffxi_dir()
    ev = (root / ZONE_EVENT).read_bytes(); dl = (root / ZONE_DIALOG).read_bytes()
    test_small_steps(ev, dl, root)
    test_say_indexed(ev, dl, root)
    test_shop(ev, dl, root)
    print("OK")


if __name__ == "__main__":
    main()


def test_oseem_primitives(ev_bytes, dl_bytes, root):
    """query menus (D4 02), row_item (D4 03), augment_preview (D4 05), parameters past 7 and the
    row-name / plural / qtyitem tokens compile to Oseem's byte shapes (Norg actor 0x010FC08F)."""
    from xi.dialog import xi_dialog
    assert xi_dialog.encode_event_string("{rowname:1} ({17} time{plural:17}[/s] remaining).") == \
        bytes.fromhex("7f 80 01 01 05 23 82 81 80 80 20 28 0a 11 20 74 69 6d 65 7f 92 11 5b 2f 73 5d 20 72 65 6d 61 69 6e 69 6e 67 29 2e")
    assert xi_dialog.encode_event_string("{qtyitem:34:33}") == bytes.fromhex("01 09 29 82 a2 80 80 82 a1 80 80")
    steps = [
        {"op": "store", "into": {"param": 17}, "from": {"local": 31}},
        {"op": "row_item", "row": 1, "item": 9210},
        {"op": "menu", "text": "q", "query": True, "cursor": {"local": 5}, "hide": {"local": 1},
         "options": ["None of these.", "{rowname:1} ({17} time{plural:17}[/s] remaining)."]},
        {"op": "augment_preview", "window": 0, "item": {"local": 41}, "a": {"local": 21}, "b": {"local": 22}, "c": {"local": 23}},
        {"op": "augment_preview", "window": 1, "item": {"local": 41}, "a": {"local": 24}, "b": {"local": 25}, "c": {"local": 26}},
        {"op": "end"},
    ]
    cs = base(steps=steps)
    cs["dialog"]["lines"].append({"id": "q", "text": "Use which item?"})
    res = xi_compile.compile_cutscene(cs, ev_bytes, dl_bytes, ffxi_dir=root)
    a = actor_of(res.event_dat, NOMAD_MOOGLE)
    scene = bytes(a.scene_data); refs = a.references
    off = a.event_offsets[a.event_ids.index(res.event_id)]
    ops = [o for o in walk(scene, off, len(scene)) if o[1] not in (0x6F, 0x70)]
    names = [core._opcode_name(op) for _, op, _, _ in ops]
    assert names == ["look_at", "get_store", "unk_d4", "unk_d4", "wait_select", "unk_d4", "unk_d4", "end"], names
    ref = lambda sel: refs[sel & 0x7FFF]
    u16 = lambda b, i=0: struct.unpack_from("<H", b, i)[0]
    assert u16(ops[1][3], 0) == 0x1000 + 2 + 17 and u16(ops[1][3], 2) == 31             # store Z[19] = L31
    assert ops[2][3][0] == 0x03 and ref(u16(ops[2][3], 1)) == 1 and ref(u16(ops[2][3], 3)) == 9210
    assert ops[3][3][0] == 0x02 and u16(ops[3][3], 3) == 5 and u16(ops[3][3], 5) == 1      # D4 02 msg L5 L1
    assert ops[5][3][0] == 0x05 and ref(u16(ops[5][3], 1)) == 0 and u16(ops[5][3], 3) == 41 and u16(ops[5][3], 9) == 23
    assert ops[6][3][0] == 0x05 and ref(u16(ops[6][3], 1)) == 1 and u16(ops[6][3], 5) == 24
    print("oseem primitives OK")


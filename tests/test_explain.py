"""Explain / survey / entity-name / codec checks.

Run:  uv run python tests/test_explain.py      (needs FFXI_DIR in .env)
"""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xi.dialog import xi_dialog                      # noqa: E402
from xi.event import xi_explain as X                 # noqa: E402


def ffxi_dir() -> Path:
    d = os.environ.get("FFXI_DIR")
    if not d:
        for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("FFXI_DIR="):
                d = line.split("=", 1)[1].strip().strip('"').strip("'")
    assert d, "FFXI_DIR not set"
    return Path(d)


def test_codec():
    b = xi_dialog.encode_event_string("Buy {item:0} for {1} gil?\\nYes.\\nNo.")
    assert b == b"Buy \x01\x05\x25\x82\x80\x80\x80 for \x0a\x01 gil?\x07Yes.\x07No.", b.hex()
    b = xi_dialog.encode_event_string("{name:0x36:2} needed")
    assert b.startswith(b"\x01\x05\x36\x82\x82\x80\x80"), b.hex()
    print("codec OK")


def test_entity_records():
    zone = 243
    base = 0x01000000 | (zone << 12)
    data = b"".join(n.encode().ljust(28, b"\x00") + struct.pack("<I", i) for n, i in
                    (("none", 0), ("A", base + 1), ("B", base + 2), ("C", base + 5)))
    assert [sid for sid, _ in X.entity_records(data)] == [0, base + 1, base + 2, base + 5]
    assert X.next_free_entity_id(data, zone) == base + 6
    assert X.next_free_entity_id(data, zone, gap=10) == base + 15
    out = X.add_entity_name(data, base + 3, "Mid")
    ids = [sid for sid, _ in X.entity_records(out)]
    assert ids == [0, base + 1, base + 2, base + 3, base + 5], ids
    out2 = X.add_entity_name(out, base + 20, "Tail")
    assert X.entity_records(out2)[-1] == (base + 20, "Tail")
    try:
        X.add_entity_name(out2, base + 3, "Dup")
        raise AssertionError("duplicate id accepted")
    except ValueError:
        pass
    assert X.entity_records(X.add_entity_name(out2, base + 3, "New", replace=True))[3] == (base + 3, "New")
    print("entity records OK")


def test_explain_isakoth(root: Path):
    zf = X.zone_files(root, [235])[0]
    actors, blobs, names = X.load_zone(zf)
    a = next(x for x in actors if x.actor_id == 0x010EB0B1)
    L = X.explain_actor(bytes(a.scene_data), list(a.references), list(a.event_ids), list(a.event_offsets), blobs, names,
                        only_event=26)
    text = "\n".join(L.lines)
    assert "Exchange for what? (Sparks: {0})" in text
    assert text.count("Subroutine @") == 4, text.count("Subroutine @")
    assert "Subroutine @1866..1e7b" in text
    assert "open query window" in text and "send 0x05B" in text and "item window" in text
    import re
    assert re.search(r"table@0d86 \[4181(?: \(item [^)]*\))?, 4182(?: \(item [^)]*\))?, 4064", text), "0x9D table expansion missing"
    for f in ("menu", "server round trip", "item window", "query window", "data tables (shop/list)", "bit fields"):
        assert f in L.features, f
    print(f"explain Isakoth OK ({len(L.lines)} lines)")


def test_survey_one_zone(root: Path):
    zf = X.zone_files(root, [235])[0]
    hits = list(X.survey_zone(zf, 0x71, 0x12))
    assert hits, "no sized number windows in Bastok Markets"
    vw = [h for h in hits if h.actor == "Voidwatch Purveyor"]
    assert vw and "1 2" in vw[0].operands and "up to" in vw[0].previous_text, vw[:1]
    print(f"survey OK ({len(hits)} hits in Bastok Markets)")


def test_lint(root: Path):
    from xi.event import xi_lint, xi_compile
    zf = X.zone_files(root, [235])[0]
    actors, blobs, names = X.load_zone(zf)
    a = next(x for x in actors if x.actor_id == 0x010EB0B1)
    res = xi_lint.lint_actor(a, blobs, 26)[26]
    assert res.ok, res.errors
    assert res.opcodes > 150 and len(res.calls) >= 4, (res.opcodes, res.calls)
    # a compiled event with a menu lints clean; a corrupted jump is caught
    zf2 = X.zone_files(root, [243])[0]
    ev = zf2.event.read_bytes(); dl = zf2.dialog.read_bytes()
    cut = {"schema": "xi.cutscene.v1", "eventId": "auto", "actor": "m",
           "cast": {"cast": [{"id": "m", "entity": "0x010F3075"}]},
           "dialog": {"lines": [{"id": "q", "text": "Reset?"}]}, "flags": {"cinematic": False},
           "steps": [{"op": "menu", "text": "q", "options": ["Yes", "No"]},
                     {"op": "branch", "cases": {"0": "y"}, "default": "n"},
                     {"op": "end", "result": 1, "label": "y"}, {"op": "end", "result": 0, "label": "n"}]}
    out = xi_compile.compile_cutscene(cut, ev, dl, ffxi_dir=root)
    lr = xi_lint.lint_dat(out.event_dat, out.dialog_dat, 0x010F3075, out.event_id)[out.event_id]
    assert lr.ok and not lr.warnings, (lr.errors, lr.warnings)
    actor = next(x for x in xi_explain_core_parse(out.event_dat) if x.actor_id == 0x010F3075)
    scene = bytearray(actor.scene_data)
    off = actor.event_offsets[actor.event_ids.index(out.event_id)]
    # first opcodes: look_at (9 bytes), 6F, 70 (turn waits), dialog_menu (7), wait_select (1), then `if` at +19:
    # corrupt its target
    struct.pack_into("<H", scene, off + 19 + 6, 0xFFF0)
    bad = xi_lint.lint_event(bytes(scene), list(actor.references), off, len(scene), xi_dialog.raw_entry_blobs(out.dialog_dat)[0])
    assert any("leaves the event" in e for e in bad.errors), bad.errors
    print("lint OK")


def xi_explain_core_parse(data):
    from xi.event import xi_event as core
    return core.parse_raw_actors(data)


def main():
    test_codec()
    test_entity_records()
    root = ffxi_dir()
    test_explain_isakoth(root)
    test_survey_one_zone(root)
    test_lint(root)
    print("OK")


if __name__ == "__main__":
    main()

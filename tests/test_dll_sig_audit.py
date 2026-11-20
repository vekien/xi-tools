"""xi dll ffximain sig-audit: pattern extraction, matching and the report, on synthetic bytes."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.dll import sig_audit as SA
from xi.dll.xi_sig_audit import cmd

BASE = SA.BASE
TEXT_VA, RDATA_VA, RELOC_VA = 0x1000, 0x2000, 0x3000


def _text(field: int) -> bytes:
    """A caller, then f1 (reads a global's field ``field``) at +0x10, f2 (returns 1) at +0x30."""
    t = bytearray(b"\xcc" * 0x60)
    t[0:5] = b"\xe8" + struct.pack("<i", 0x10 - 5)            # call f1
    t[5:10] = b"\xe8" + struct.pack("<i", 0x30 - 10)          # call f2
    t[10] = 0xC3
    f1 = (b"\x55\x8b\xec"                                     # push ebp; mov ebp, esp
          + b"\x8b\x0d" + struct.pack("<I", BASE + RDATA_VA)  # mov ecx, [global]
          + bytes([0x8B, 0x41, field])                        # mov eax, [ecx + field]
          + b"\x85\xc0\x74\x02\x33\xc0\x5d\xc3")              # test; jz; xor; pop; ret
    t[0x10:0x10 + len(f1)] = f1
    f2 = b"\x55\x8b\xec\xb8\x01\x00\x00\x00\x5d\xc3"
    t[0x30:0x30 + len(f2)] = f2
    return bytes(t)


def _pe(text: bytes, stamp: int = 0x5F000000, packed: bool = False) -> bytes:
    """A minimal PE32: .text, .rdata holding a pointer to f2 (relocated), .reloc."""
    e = 0x40
    opt = e + 24
    sec = opt + 0xE0
    raw_text, raw_rdata, raw_reloc = 0x400, 0x600, 0x800
    d = bytearray(0xA00)
    d[0:2] = b"MZ"
    struct.pack_into("<I", d, 0x3C, e)
    d[e:e + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", d, e + 4, 0x14C, 3, stamp, 0, 0, 0xE0, 0x2102)
    struct.pack_into("<H", d, opt, 0x10B)
    struct.pack_into("<I", d, opt + 28, BASE)
    struct.pack_into("<I", d, opt + 56, 0x4000)
    struct.pack_into("<I", d, opt + 92, 16)
    struct.pack_into("<II", d, opt + 96 + 5 * 8, RELOC_VA, 12)
    sections = [(b".text", TEXT_VA, len(text), 0 if packed else 0x200, 0 if packed else raw_text),
                (b".rdata", RDATA_VA, 4, 0x200, raw_rdata),
                (b".reloc", RELOC_VA, 12, 0x200, raw_reloc)]
    for k, (name, va, vsize, rsize, raw) in enumerate(sections):
        struct.pack_into("<8sIIII", d, sec + k * 40, name, vsize, va, rsize, raw)
    if not packed:
        d[raw_text:raw_text + len(text)] = text
    struct.pack_into("<I", d, raw_rdata, BASE + TEXT_VA + 0x30)        # &f2
    struct.pack_into("<IIHH", d, raw_reloc, RDATA_VA, 12, 0x3000, 0)   # HIGHLOW at .rdata+0
    return bytes(d)


@pytest.fixture
def images(tmp_path):
    old, new = tmp_path / "old.dll", tmp_path / "new.dll"
    old.write_bytes(_pe(_text(0x10)))
    new.write_bytes(_pe(_text(0x14), stamp=0x60000000))
    return old, new


SOURCE = '''
// "DE AD BE EF 00" in a comment is not a pattern
/* nor "8B 41 10 85 C0" in a block
   comment */
auto a = scan::find("8B 0D ?? ?? ?? ?? 8B 41 10");        // lost in new
auto b = scan::find("8B 0D ?? ?? ?? ?? "
                    "8B 41");                              // joined, function changed
auto c = scan::find("55 8B EC B8 01 00 00 00");            // unchanged
auto d = scan::find("8B 41 14 85 C0");                     // only in new
auto e = scan::find("DE AD BE EF");                        // in neither
auto f = scan::find("8B 41 10 85 C0 | 8B 41 14 85 C0");    // alternatives
auto i = scan::find("55 8B EC ??");                        // several matches
auto g = "AB CD";                                          // too short to be a pattern
auto h = "hello world";
'''


@pytest.fixture
def src(tmp_path):
    root = tmp_path / "src"
    (root / "core").mkdir(parents=True)
    (root / "core" / "hooks.cpp").write_text(SOURCE, encoding="utf-8")
    (root / "core" / "more.hpp").write_text('const char* p = "55 8B EC B8 01 00 00 00";\n', encoding="utf-8")
    (root / "notes.txt").write_text('"8B 0D ?? ?? ?? ?? 8B 41 10"\n', encoding="utf-8")
    return root


# ── the source ───────────────────────────────────────────────────────────────

def test_as_pattern():
    assert SA.as_pattern("8B 0D ?? ?? 85 C9") == [0x8B, 0x0D, None, None, 0x85, 0xC9]
    assert SA.as_pattern("8B0D??85C9") == [0x8B, 0x0D, None, 0x85, 0xC9]
    assert SA.as_pattern("8B 0D ? ? 85 C9") == [0x8B, 0x0D, None, None, 0x85, 0xC9]
    assert SA.as_pattern("?? ?? ?? 85 C9") is None          # two literal bytes
    assert SA.as_pattern("AB CD") is None                   # too short
    assert SA.as_pattern("hello world") is None
    assert SA.as_alternatives("8B 41 10 85 | 8B 41 14 85") == [[0x8B, 0x41, 0x10, 0x85], [0x8B, 0x41, 0x14, 0x85]]
    assert SA.as_alternatives("8B 41 10 85 | nope") is None


def test_literals_skip_comments_and_join_adjacent():
    lits = SA.literals(SOURCE)
    texts = [t for _, t in lits]
    assert "DE AD BE EF 00" not in texts
    assert "8B 41 10 85 C0" not in texts
    assert "8B 0D ?? ?? ?? ?? 8B 41" in texts
    line = next(n for n, t in lits if t == "8B 0D ?? ?? ?? ?? 8B 41")
    assert line == 6                                        # where the first piece starts


def test_source_patterns(src):
    found = SA.source_patterns(src)
    texts = {" | ".join(SA.pattern_text(a) for a in alts): where for alts, where in found.items()}
    assert texts["55 8B EC B8 01 00 00 00"] == ["core/hooks.cpp:8", "core/more.hpp:1"]
    assert texts["8B 0D ?? ?? ?? ?? 8B 41 10"] == ["core/hooks.cpp:5"]   # notes.txt isn't source
    assert "8B 41 10 85 C0 | 8B 41 14 85 C0" in texts
    assert len(found) == 7


# ── matching ─────────────────────────────────────────────────────────────────

def test_first_match_wildcards_and_alternatives(images):
    old = SA.Image(images[0])
    assert not old.packed()
    assert old.relocs == {RDATA_VA}
    pat, at = SA.first_match(old, [SA.as_pattern("8B 0D ?? ?? ?? ?? 8B 41 10")])
    assert at == [TEXT_VA + 0x13]
    pat, at = SA.first_match(old, SA.as_alternatives("8B 41 14 85 C0 | 8B 41 10 85 C0"))
    assert pat == SA.as_pattern("8B 41 10 85 C0") and at == [TEXT_VA + 0x19]
    assert SA.first_match(old, [SA.as_pattern("DE AD BE EF")]) == (None, [])


def test_function_starts_from_calls_and_relocated_pointers(images):
    old = SA.Image(images[0])
    assert SA.function_starts(old) == [TEXT_VA + 0x10, TEXT_VA + 0x30]


def test_displacement_changes():
    a = ["mov eax, dword ptr [ecx + 0x10]", "push 0x10", "mov eax, dword ptr [esp + 0x8]"]
    b = ["mov eax, dword ptr [ecx + 0x14]", "push 0x14", "mov eax, dword ptr [esp + 0xc]"]
    assert list(SA.displacement_changes(a, b)) == [(0x10, 0x14)]


# ── the audit and report ─────────────────────────────────────────────────────

def test_audit(images, src):
    old, new = SA.Image(images[0]), SA.Image(images[1])
    r = SA.audit(old, new, SA.source_patterns(src))
    lost = {x["text"]: x for x in r["lost"]}
    assert list(lost) == ["8B 0D ?? ?? ?? ?? 8B 41 10"]
    info = lost["8B 0D ?? ?? ?? ?? 8B 41 10"]["lost"]
    assert info["old_fn"] == info["new_fn"] == TEXT_VA + 0x10
    assert info["bad"] == [8] and info["old_bytes"] == b"\x10" and info["new_bytes"] == b"\x14"
    assert [x["text"] for x in r["gained"]] == ["8B 41 14 85 C0"]
    assert [x["text"] for x in r["neither"]] == ["DE AD BE EF"]
    changed = {c["row"]["text"] for c in r["changed"]}
    assert "8B 0D ?? ?? ?? ?? 8B 41" in changed
    assert "55 8B EC B8 01 00 00 00" not in changed
    assert r["moved"] == []
    assert r["status"] == {"changed": 1, "same": 1}


def test_run_writes_report(images, src, tmp_path):
    out = tmp_path / "out"
    r = SA.run(images[0], images[1], src, out)
    text = (out / "report.md").read_text(encoding="utf-8")
    assert text == r["report"]
    assert "7 distinct hex patterns in the source: 4 found in both builds, **1 only in old" in text
    assert "### Found in old, not in new" in text
    assert "`core/hooks.cpp:5`  `8B 0D ?? ?? ?? ?? 8B 41 10`" in text
    assert "the pattern's bytes that differ there: [8] (old 10 new 14)" in text
    assert "## Functions under the matching patterns" in text
    assert "    - 0x10001019 mov eax, dword ptr [ecx + 0x10]" in text
    assert "    + 0x10001019 mov eax, dword ptr [ecx + 0x14]" in text
    assert "Functions aligned: 1 the same, 1 changed, 0 added, 0 removed." in text
    assert "### Several matches" not in text
    assert "### Several matches" in SA.run(images[0], images[1], src, out, verbose=True)["report"]
    assert not list(out.glob("*_unpacked.dll"))             # unpacked input is read as it is


def test_packed_input_that_does_not_unpack(tmp_path, images, src):
    packed = tmp_path / "packed.dll"
    packed.write_bytes(_pe(_text(0x10), packed=True))
    assert SA.Image(packed).packed()
    with pytest.raises(SA.SigAuditError, match="could not unpack"):
        SA.run(packed, images[1], src, tmp_path / "out")


def test_not_a_pe(tmp_path, images, src):
    bad = tmp_path / "bad.dll"
    bad.write_bytes(b"not a dll at all")
    with pytest.raises(SA.SigAuditError, match="not a PE"):
        SA.run(bad, images[1], src, tmp_path / "out")


def test_cmd(images, src, tmp_path):
    runner = CliRunner()
    res = runner.invoke(cmd, [str(images[0]), str(images[1]), "--src", str(src),
                              "--output-dir", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert "# FFXiMain.dll signature audit" in res.output
    assert (tmp_path / "out" / "report.md").is_file()

    bad = tmp_path / "bad.dll"
    bad.write_bytes(b"nope")
    res = runner.invoke(cmd, [str(bad), str(images[1]), "--src", str(src),
                              "--output-dir", str(tmp_path / "out")])
    assert res.exit_code == 1 and "not a PE" in res.output

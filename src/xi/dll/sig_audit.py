"""Library for ``xi dll ffximain sig-audit``: check a C++ project's byte patterns against two FFXiMain.dll builds.

A project that hooks the client by byte pattern (``"8B 0D ?? ?? ?? ?? 85 C9"`` strings in
its source, found with a scanner at run time) breaks quietly when the client is rebuilt:
a pattern that no longer matches leaves its feature without a hook, and a pattern that
still matches can sit in a function whose fields moved, so any offset the source adds to
the match reads the wrong thing. This audits that offline, over two builds of the DLL:

1. every hex pattern string in the source tree is searched for in both builds
   (``sig.scan``, the scanner ``sig-apply`` uses);
2. each match is followed to the function it sits in, and that function is compared
   with the one the new build has in the same place (instructions, with branch targets
   and image addresses masked);
3. all functions are aligned between the builds, which gives the structure offsets that
   moved, by how much and in what range.

Both DLLs are unpacked first when they are the game's POL1-packed files
(``pol1_io.unpack_dll``); nothing is loaded or run, and the game install isn't written.
See ``docs/dll/sig-audit.md``.
"""
from __future__ import annotations

import difflib
import hashlib
import re
import struct
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

from xi.dll import sig

BASE = 0x10000000           # FFXiMain's preferred image base; addresses are reported against it
IMAGE_SPAN = 0xC20000       # immediates in [BASE, BASE + IMAGE_SPAN) are image addresses, masked
WINDOW = 700                # instructions compared under a pattern
SOURCE_SUFFIXES = (".cpp", ".hpp", ".inc", ".h", ".c")

HEX = re.compile(r"0x[0-9a-f]+")
DISP = re.compile(r"(?<![\w])0x[0-9a-f]+(?![\w])")
MEM = re.compile(r"\[[^\]]*\]")


class SigAuditError(Exception):
    """A DLL or source tree the audit can't read."""


# ── the C++ source ───────────────────────────────────────────────────────────

def literals(text: str) -> list[tuple[int, str]]:
    """(line, contents) of each C++ string literal, adjacent ones joined, comments skipped."""
    i, n, line = 0, len(text), 1
    pending, pending_line, out = None, 0, []
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += text.count("\n", i, j)
            i = j
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            if pending is None:
                pending, pending_line = [], line
            pending.append("".join(buf))
            i = j + 1
        else:
            if c == "'":
                j = i + 1
                while j < n and text[j] != "'":
                    j += 2 if text[j] == "\\" else 1
                i = j + 1
            else:
                i += 1
            if pending is not None:
                out.append((pending_line, "".join(pending)))
                pending = None
    if pending is not None:
        out.append((pending_line, "".join(pending)))
    return out


def as_pattern(s: str) -> list[int | None] | None:
    """A hex byte pattern's bytes (None for ``??`` or ``?``), or None if the string isn't one.

    At least four bytes, three of them literal, so ordinary strings ("AB CD") don't count.
    """
    s = s.strip()
    if len(s) < 8:
        return None
    toks: list[int | None] = []
    i = 0
    while i < len(s):
        if s[i] == " ":
            i += 1
        elif s[i] == "?":
            toks.append(None)
            i += 2 if s[i + 1:i + 2] == "?" else 1
        elif re.fullmatch(r"[0-9A-Fa-f]{2}", s[i:i + 2]):
            toks.append(int(s[i:i + 2], 16))
            i += 2
        else:
            return None
    return toks if len(toks) >= 4 and sum(t is not None for t in toks) >= 3 else None


def as_alternatives(s: str) -> list[list[int | None]] | None:
    """The alternatives of a pattern (``"A | B"``: the same place as two builds compile it),
    or None if any part isn't a pattern."""
    alts = [as_pattern(part) for part in s.split("|")]
    return alts if all(a is not None for a in alts) else None


def source_patterns(src: Path) -> dict[tuple[tuple[int | None, ...], ...], list[str]]:
    """{alternatives: ["file:line", …]} for each hex pattern string in the source tree."""
    src = Path(src)
    found: dict[tuple[tuple[int | None, ...], ...], list[str]] = {}
    for path in sorted(src.rglob("*")):
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(src).as_posix()
        for line, lit in literals(path.read_text(encoding="utf-8", errors="replace")):
            if (alts := as_alternatives(lit)) is not None:
                found.setdefault(tuple(tuple(a) for a in alts), []).append(f"{rel}:{line}")
    return found


def pattern_text(pat) -> str:
    return " ".join("??" if t is None else f"{t:02X}" for t in pat)


# ── the DLLs ─────────────────────────────────────────────────────────────────

class Image:
    """An unpacked FFXiMain.dll: its sections, relocations and ``.text``."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.source = self.path      # the DLL as given (``path`` is its unpacked copy when it was packed)
        self.data = d = self.path.read_bytes()
        try:
            e = struct.unpack_from("<I", d, 0x3C)[0]
            if d[:2] != b"MZ" or d[e:e + 4] != b"PE\0\0":
                raise SigAuditError(f"{path} is not a PE file")
            nsec, = struct.unpack_from("<H", d, e + 6)
            optsize, = struct.unpack_from("<H", d, e + 20)
            self.stamp, = struct.unpack_from("<I", d, e + 8)
            opt = e + 24
            self.image_size, = struct.unpack_from("<I", d, opt + 56)
            self.secs = []
            for i in range(nsec):
                name, vsize, va, rsize, raw = struct.unpack_from("<8sIIII", d, opt + optsize + i * 40)
                self.secs.append((name.rstrip(b"\0").decode("latin-1"), va, vsize, raw, rsize))
            self.relocs = self._relocs(*struct.unpack_from("<II", d, opt + 96 + 5 * 8))
        except struct.error as exc:
            raise SigAuditError(f"{path} is not a PE file ({exc})") from exc
        text = next((s for s in self.secs if s[0] == ".text"), None)
        if text is None:
            raise SigAuditError(f"{path} has no .text section")
        _, self.text_va, _, raw, rsize = text
        self.text = d[raw:raw + rsize]

    def packed(self) -> bool:
        """The game's file: ``.text`` has no bytes on disk (the POL1 stub fills it at load)."""
        return not self.text

    def file_off(self, rva: int) -> int | None:
        for _, va, vsize, raw, rsize in self.secs:
            if va <= rva < va + max(vsize, rsize):
                return raw + rva - va
        return None

    def _relocs(self, rva: int, size: int) -> set[int]:
        off, out = self.file_off(rva), set()
        if off is None or not size:
            return out
        end = off + size
        while off + 8 <= end:
            page, blk = struct.unpack_from("<II", self.data, off)
            if blk < 8:
                break
            for k in range((blk - 8) // 2):
                w, = struct.unpack_from("<H", self.data, off + 8 + k * 2)
                if w >> 12 == 3:     # IMAGE_REL_BASED_HIGHLOW
                    out.add(page + (w & 0xFFF))
            off += blk
        return out

    def describe(self, name: str) -> str:
        return f"{name}: header {self.stamp:#010x}, image {self.image_size:#x}, .text {len(self.text):#x} bytes"


def load(path: Path, output_dir: Path, label: str) -> Image:
    """The image at ``path``, unpacked into ``output_dir`` as ``<label>_<stem>_unpacked.dll``
    first when it is the game's packed file; an unpacked DLL is read as it is."""
    from xi.dll.pol1_io import unpack_dll

    image = Image(path)
    if not image.packed():
        return image
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{label}_{Path(path).stem}_unpacked.dll"
    try:
        unpack_dll(Path(path), target)
    except Exception as exc:
        raise SigAuditError(f"{path}: could not unpack its POL1 .text ({exc})") from exc
    unpacked = Image(target)
    unpacked.source = Path(path)
    return unpacked


def first_match(img: Image, alts) -> tuple[list[int | None] | None, list[int]]:
    """The first alternative that matches in ``img`` (as a scanner tries them in order),
    and its matches as RVAs."""
    for alt in alts:
        hits = sig.scan(img.text, pattern_text(alt))
        if hits:
            return list(alt), [img.text_va + x for x in hits]
    return None, []


# ── code ─────────────────────────────────────────────────────────────────────

def disassembler():
    import capstone

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.skipdata = True
    return md


def norm(ins) -> str:
    """An instruction with its branch target and image addresses masked."""
    mn = ins.mnemonic
    if mn == "call" or mn.startswith("j") or mn.startswith("loop"):
        return mn + " T"
    return HEX.sub(lambda m: "ADDR" if BASE <= int(m.group(0), 16) < BASE + IMAGE_SPAN else m.group(0),
                   f"{mn} {ins.op_str}")


def function_at(md, img: Image, rva: int, limit: int = WINDOW) -> list[tuple[int, str, str]]:
    """[(address, normalised, text)] from ``rva`` to the first ret followed by padding."""
    off = rva - img.text_va
    out, ret = [], False
    for i in md.disasm(img.text[off:off + limit * 12], BASE + rva):
        if ret and i.mnemonic in ("nop", "int3"):
            break
        ret = i.mnemonic == "ret"
        out.append((i.address, norm(i), f"{i.mnemonic} {i.op_str}"))
        if len(out) >= limit:
            break
    return out


def function_starts(img: Image) -> list[int]:
    """Direct call targets, and text pointers in relocated data (vtables, callbacks): the
    functions, as RVAs."""
    text, lo = img.text, img.text_va
    hi, found = lo + len(text), set()
    pos = text.find(b"\xe8")
    while pos >= 0:
        if pos + 5 <= len(text):
            target = lo + pos + 5 + struct.unpack_from("<i", text, pos + 1)[0]
            if lo <= target < hi and ((target & 0xF) == 0
                                      or text[target - lo - 1:target - lo] in (b"\xcc", b"\x90", b"\xc3")):
                found.add(target)
        pos = text.find(b"\xe8", pos + 1)
    for rva in img.relocs:
        off = img.file_off(rva)
        sec = next((s for s in img.secs if s[1] <= rva < s[1] + s[2]), None)
        if off is not None and sec and sec[0] != ".text" and off + 4 <= len(img.data):
            val = struct.unpack_from("<I", img.data, off)[0] - BASE
            if lo <= val < hi:
                found.add(val)
    return sorted(found)


def functions(md, img: Image) -> list[tuple[int, int, str, list[str]]]:
    starts, out = function_starts(img), []
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else img.text_va + len(img.text)
        code = img.text[a - img.text_va:b - img.text_va]
        ins = [norm(i) for i in md.disasm(code, BASE + a)]
        out.append((a, b, hashlib.md5("\n".join(ins).encode()).hexdigest(), ins))
    return out


def displacement_changes(a: list[str], b: list[str]):
    """(old, new) for each constant in a memory operand that differs between same-shaped
    instructions."""
    for x, y in zip(a, b):
        if x == y:
            continue
        xs, ys = DISP.findall(x), DISP.findall(y)
        if len(xs) == len(ys) and DISP.sub("#", x) == DISP.sub("#", y):
            mem = " ".join(MEM.findall(x))
            for p, q in zip(xs, ys):
                if p != q and p in mem and "esp" not in mem:
                    yield int(p, 16), int(q, 16)


def align(md, old: Image, new: Image):
    """({old function rva: new function rva}, {(old, new) constant: instructions}, {status: count})."""
    fo, fn = functions(md, old), functions(md, new)
    sm = difflib.SequenceMatcher(None, [f[2] for f in fo], [f[2] for f in fn], autojunk=False)
    pairs: dict[int, int] = {}
    shifts: Counter = Counter()
    status: Counter = Counter()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                pairs[fo[i1 + k][0]] = fn[j1 + k][0]
            status["same"] += i2 - i1
        elif tag == "replace" and i2 - i1 == j2 - j1:
            for x, y in zip(fo[i1:i2], fn[j1:j2]):
                pairs[x[0]] = y[0]
                status["changed"] += 1
                if len(x[3]) == len(y[3]):
                    shifts.update(displacement_changes(x[3], y[3]))
        else:
            status["added"] += j2 - j1
            status["removed"] += i2 - i1
    return pairs, shifts, status


def in_table(img: Image, address: int) -> bool:
    """Whether the instruction at ``address`` overlaps an address the image relocates: data
    (a switch's table), not code."""
    return any(address - BASE + k in img.relocs for k in range(-3, 4))


def containing(starts: list[int], rva: int) -> int | None:
    k = bisect_right(starts, rva) - 1
    return starts[k] if k >= 0 else None


def clusters(values: list[int]) -> list[list[int]]:
    out, cur = [], [values[0]]
    for v in values[1:]:
        if v - cur[-1] <= 0x100:
            cur.append(v)
        else:
            out.append(cur)
            cur = [v]
    return out + [cur]


# ── the audit ────────────────────────────────────────────────────────────────

def audit(old: Image, new: Image, patterns: dict) -> dict:
    """Compare ``patterns`` (from :func:`source_patterns`) across two unpacked images.

    Returns the rows by outcome, the functions under the patterns that differ, and the
    whole-binary alignment (status counts and the offsets that moved).
    """
    md = disassembler()
    rows = []
    for alts, where in patterns.items():
        old_pat, old_at = first_match(old, alts)
        _, new_at = first_match(new, alts)
        rows.append({"pat": old_pat or list(alts[0]),
                     "text": " | ".join(pattern_text(a) for a in alts),
                     "where": where, "old": old_at, "new": new_at})
    both = [r for r in rows if r["old"] and r["new"]]
    lost = [r for r in rows if r["old"] and not r["new"]]
    gained = [r for r in rows if not r["old"] and r["new"]]
    neither = [r for r in rows if not r["old"] and not r["new"]]
    many = [r for r in both if len(r["old"]) > 1 or len(r["new"]) > 1]

    pairs, shifts, status = align(md, old, new)
    old_starts, new_starts = function_starts(old), function_starts(new)

    for r in lost:
        fn = containing(old_starts, r["old"][0])
        r["lost"] = None
        if fn in pairs:
            rel = r["old"][0] - fn
            at = pairs[fn] + rel - new.text_va
            bytes_new = new.text[at:at + len(r["pat"])]
            bad = [i for i, t in enumerate(r["pat"])
                   if t is not None and (i >= len(bytes_new) or bytes_new[i] != t)]
            r["lost"] = {"old_fn": fn, "new_fn": pairs[fn], "bad": bad,
                         "old_bytes": bytes(r["pat"][i] for i in bad[:6]),
                         "new_bytes": bytes(bytes_new[i] for i in bad[:6] if i < len(bytes_new))}

    moved = []
    for r in both:
        fo, fn = containing(old_starts, r["old"][0]), containing(new_starts, r["new"][0])
        if fo in pairs and pairs[fo] != fn:
            moved.append(r)

    changed = []
    for r in sorted(both, key=lambda r: r["old"][0]):
        a, b = function_at(md, old, r["old"][0]), function_at(md, new, r["new"][0])
        sm = difflib.SequenceMatcher(None, [x[1] for x in a], [x[1] for x in b], autojunk=False)
        # a switch's table of addresses sits in .text and decodes as nonsense, differing
        # only by the addresses in it
        ops = [o for o in sm.get_opcodes() if o[0] != "equal"
               and not (all(in_table(old, x[0]) for x in a[o[1]:o[2]])
                        and all(in_table(new, y[0]) for y in b[o[3]:o[4]]))]
        if ops:
            changed.append({"row": r, "old_len": len(a), "new_len": len(b),
                            "ratio": sm.ratio(), "ops": ops, "old_code": a, "new_code": b})

    by = defaultdict(Counter)
    for (p, q), n in shifts.items():
        by[q - p][p] += n
    moved_offsets = []
    for delta, cnt in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))[:8]:
        if sum(cnt.values()) < 8:
            continue
        moved_offsets.append({"shift": delta, "instructions": sum(cnt.values()),
                              "ranges": [(c[0], c[-1]) for c in clusters(sorted(cnt))[:6]]})

    return {"rows": rows, "both": both, "lost": lost, "gained": gained, "neither": neither,
            "many": many, "moved": moved, "changed": changed, "status": dict(status),
            "offsets": moved_offsets}


def render(result: dict, old: Image, new: Image, verbose: bool = False) -> str:
    """The audit as a Markdown report, read top down."""
    out: list[str] = []
    p = out.append

    def where(r):
        return ", ".join(r["where"][:3])

    rows, both, lost = result["rows"], result["both"], result["lost"]
    p("# FFXiMain.dll signature audit\n")
    p(f"- {old.describe('old')}  ({old.source})")
    p(f"- {new.describe('new')}  ({new.source})\n")

    p("## Patterns\n")
    p(f"{len(rows)} distinct hex patterns in the source: {len(both)} found in both builds, "
      f"**{len(lost)} only in old (would miss in new)**, {len(result['gained'])} only in new, "
      f"{len(result['neither'])} in neither (other modules, data, or code the project patches in "
      f"at run time), {len(result['many'])} with several matches (the first is used).\n")

    if lost:
        p("### Found in old, not in new\n")
        for r in lost:
            p(f"- `{where(r)}`  `{r['text'][:90]}`")
            info = r["lost"]
            if info:
                p(f"  - its function is {info['old_fn'] + BASE:#x} in old, {info['new_fn'] + BASE:#x} in new; "
                  f"the pattern's bytes that differ there: {info['bad']} "
                  f"(old {info['old_bytes'].hex(' ')} new {info['new_bytes'].hex(' ')})")
            else:
                p("  - its function was rewritten in new: look for the code by hand")
        p("")

    if result["moved"]:
        p("### Matches, but in a different function than the old one's counterpart\n")
        p("A pattern the source scans for only in a window of code (a search limited to one range) is "
          "searched whole here, so a hit")
        p("elsewhere can just be that; any other is a pattern that now finds a different function.\n")
        for r in result["moved"]:
            p(f"- `{where(r)}`  `{r['text'][:90]}`  old {r['old'][0] + BASE:#x}, new {r['new'][0] + BASE:#x}")
        p("")

    if result["many"] and verbose:
        p("### Several matches\n")
        for r in result["many"]:
            p(f"- `{where(r)}`  old {len(r['old'])}, new {len(r['new'])}: `{r['text'][:80]}`")
        p("")

    p("## Functions under the matching patterns\n")
    for c in result["changed"]:
        r = c["row"]
        p(f"- `{where(r)}`  old {r['old'][0] + BASE:#x} ({c['old_len']} instructions), "
          f"new {r['new'][0] + BASE:#x} ({c['new_len']}), similarity {c['ratio']:.3f}")
        for _tag, i1, i2, j1, j2 in c["ops"][:None if verbose else 3]:
            for x in c["old_code"][i1:min(i2, i1 + 4)]:
                p(f"    - {x[0]:#x} {x[2]}")
            for y in c["new_code"][j1:min(j2, j1 + 4)]:
                p(f"    + {y[0]:#x} {y[2]}")
    p(f"\n{len(result['changed'])} of {len(both)} functions differ in new "
      f"(the rest are instruction for instruction the same).\n")

    status = Counter(result["status"])
    p("## Offsets that moved (whole binary)\n")
    p(f"Functions aligned: {status['same']} the same, {status['changed']} changed, "
      f"{status['added']} added, {status['removed']} removed.\n")
    p("| shift | instructions | where (old offsets) |\n|---|---|---|")
    for o in result["offsets"]:
        spans = ", ".join(f"{a:#x}-{b:#x}" for a, b in o["ranges"])
        p(f"| {o['shift']:+#x} | {o['instructions']} | {spans} |")
    p("\nA constant in the source inside one of those ranges (an offset into the same structure) "
      "moved by that much.")
    return "\n".join(out) + "\n"


def run(old_dll: Path, new_dll: Path, src: Path, output_dir: Path, verbose: bool = False) -> dict:
    """Unpack both DLLs into ``output_dir`` (when packed), audit ``src``'s patterns and write
    ``output_dir/report.md``. Returns the audit result plus ``report`` and ``report_path``."""
    output_dir = Path(output_dir)
    if not Path(src).is_dir():
        raise SigAuditError(f"source folder not found: {src}")
    old = load(Path(old_dll), output_dir, "old")
    new = load(Path(new_dll), output_dir, "new")
    patterns = source_patterns(Path(src))
    result = audit(old, new, patterns)
    report = render(result, old, new, verbose)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    result.update(report=report, report_path=report_path, old_image=old.path, new_image=new.path)
    return result

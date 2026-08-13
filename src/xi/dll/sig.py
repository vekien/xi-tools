"""Signature-based patching for POL1 client DLLs.

An absolute-address `.patch` (``<va> <expect> <replace>``) is pinned to one exact
build: every function moves when the client is rebuilt, so the addresses go
stale. A **signature** locates each edit by the *surrounding code pattern* with
the volatile bytes (absolute addresses, call/jmp targets) wildcarded — so an edit
follows its function wherever the new build put it, and only genuinely *rewritten*
code fails to resolve (loudly, never silently).

Two halves:

* :func:`generate` — turn an address `.patch` + the build it targets into a
  ``.sigpatch`` (JSON). Each signature is grown until it matches **only** sites we
  intend to edit (verified against the known edit set), so it can never touch an
  unrelated location.
* :func:`apply` — scan any build for those signatures and apply (or dry-run) the
  edits. Locate-all-then-write, so co-located edits don't disturb each other.

Format is IDA-style: ``pattern`` is space-separated hex with ``??`` wildcards.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

IMG_LO, IMG_HI = 0x10000000, 0x11000000
MAX_GROW = 26  # instructions of context to add on each side before giving up


# ── address-patch parsing ────────────────────────────────────────────────────
def parse_addr_patch(text: str) -> list[tuple[int, bytes, bytes, str]]:
    out = []
    for raw in text.splitlines():
        body, _, note = raw.partition(";")
        body = body.strip()
        if not body or body.startswith("#"):
            continue
        p = body.split()
        if len(p) < 3:
            continue
        out.append((int(p[0], 16), bytes.fromhex(p[1]), bytes.fromhex(p[2]), note.strip()))
    return out


# ── PE .text access ──────────────────────────────────────────────────────────
@dataclass
class TextImage:
    data: bytearray          # whole file
    text: bytes              # .text bytes
    text_off: int            # file offset of .text
    text_va: int             # VA of .text start
    img: int                 # image base
    pe: object

    @classmethod
    def load(cls, path: Path):
        import pefile

        data = bytearray(Path(path).read_bytes())
        pe = pefile.PE(data=bytes(data), fast_load=True)
        s = next(x for x in pe.sections if x.Name.rstrip(b"\x00") == b".text")
        off = s.PointerToRawData
        return cls(
            data=data,
            text=bytes(data[off : off + s.SizeOfRawData]),
            text_off=off,
            text_va=pe.OPTIONAL_HEADER.ImageBase + s.VirtualAddress,
            img=pe.OPTIONAL_HEADER.ImageBase,
            pe=pe,
        )

    def va_to_textoff(self, va: int) -> int:
        return va - self.text_va

    def va_to_fileoff(self, va: int) -> int:
        return self.pe.get_offset_from_rva(va - self.img)


# ── disassembly + masking ────────────────────────────────────────────────────
def _boundaries(text: bytes, base: int):
    """Linear instruction boundaries: list of (addr, size), + addr→index."""
    from capstone import CS_ARCH_X86, CS_MODE_32, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.skipdata = True
    md.detail = False
    inst, idx = [], {}
    for ins in md.disasm(text, base):
        idx[ins.address] = len(inst)
        inst.append((ins.address, ins.size))
    return inst, idx


def _wildcard_ranges(ins) -> list[tuple[int, int]]:
    """Byte ranges within an instruction to wildcard: image-range imm/disp and
    call/jmp targets (which encode addresses that move between builds)."""
    import capstone.x86 as X

    out = []
    enc = getattr(ins, "encoding", None)
    for op in ins.operands:
        if op.type == X.X86_OP_IMM:
            v = op.imm & 0xFFFFFFFF
            if IMG_LO <= v < IMG_HI:
                if enc and getattr(enc, "imm_size", 0):
                    out.append((enc.imm_offset, enc.imm_size))
                else:
                    _le_find(ins, v, out)
        elif op.type == X.X86_OP_MEM:
            d = op.mem.disp & 0xFFFFFFFF
            if IMG_LO <= d < IMG_HI:
                if enc and getattr(enc, "disp_size", 0):
                    out.append((enc.disp_offset, enc.disp_size))
                else:
                    _le_find(ins, d, out)
    return out


def _le_find(ins, val: int, out: list):
    b = val.to_bytes(4, "little")
    i = bytes(ins.bytes).find(b)
    if i >= 0:
        out.append((i, 4))


def _pattern_for_window(win_bytes: bytes, win_va: int) -> list[bool]:
    """Return per-byte literal-mask (True=literal) for a window of pure code."""
    from capstone import CS_ARCH_X86, CS_MODE_32, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    mask = [True] * len(win_bytes)
    for ins in md.disasm(win_bytes, win_va):
        base = ins.address - win_va
        for off, size in _wildcard_ranges(ins):
            for k in range(off, off + size):
                if 0 <= base + k < len(mask):
                    mask[base + k] = False
    return mask


def pattern_str(sig: bytes, mask: list[bool]) -> str:
    return " ".join(f"{b:02x}" if m else "??" for b, m in zip(sig, mask))


def compile_pattern(pat: str) -> re.Pattern:
    toks = pat.split()
    parts = [b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in toks]
    return re.compile(b"".join(parts), re.DOTALL)


def pattern_len(pat: str) -> int:
    return len(pat.split())


def scan(text: bytes, pat: str) -> list[int]:
    return [m.start() for m in compile_pattern(pat).finditer(text)]


# ── generation ───────────────────────────────────────────────────────────────
def generate(src_dll: Path, addr_patch: str) -> dict:
    """Build a ``.sigpatch`` dict from an address ``.patch`` and the build it targets.

    For each ``<va> <expect> <replace>`` edit, grow an instruction window around
    the site (wildcarding image-range address operands) until the signature
    matches **only** sites the patch intends to edit — verified against the full
    edit set, so a signature can never touch an unrelated location. Identical
    sites (e.g. the repeated container allocs) dedupe to one multi-match entry.
    Edits with no safe signature keep an address-pinned fallback.

    ``src_dll`` must be the exact build ``addr_patch`` was authored against (its
    ``expect`` bytes have to match). Returns ``{"meta": {...}, "edits": [...]}``;
    persist with :func:`save_sigpatch`.
    """
    img = TextImage.load(src_dll)
    edits = parse_addr_patch(addr_patch)
    inst, idx = _boundaries(img.text, img.text_va)
    starts = [a for a, _ in inst]
    import bisect

    intended = {va: (exp, rep) for va, exp, rep, _ in edits}

    def instr_index_at(va: int) -> int:
        j = bisect.bisect_right(starts, va) - 1
        return max(0, j)

    entries = {}  # dedup key -> entry
    stats = {"unique": 0, "multi": 0, "unsafe": 0}
    unsafe = []
    for va, exp, rep, note in edits:
        i0 = instr_index_at(va)
        i1 = instr_index_at(va + len(exp) - 1)
        chosen = None
        for grow in range(2, MAX_GROW + 1):
            a = max(0, i0 - grow)
            b = min(len(inst) - 1, i1 + grow)
            win_va = inst[a][0]
            win_end = inst[b][0] + inst[b][1]
            wb = img.text[img.va_to_textoff(win_va) : img.va_to_textoff(win_end)]
            mask = _pattern_for_window(wb, win_va)
            edit_off = va - win_va
            if edit_off < 0 or edit_off + len(exp) > len(wb):
                continue
            # edit bytes must be literal (so a match guarantees `expect`)
            if not all(mask[edit_off : edit_off + len(exp)]):
                continue
            pat = pattern_str(wb, mask)
            hits = scan(img.text, pat)
            # every hit must land on an intended site carrying this same edit
            ok = True
            sites = []
            for h in hits:
                site_va = img.text_va + h + edit_off
                if intended.get(site_va) != (exp, rep):
                    ok = False
                    break
                sites.append(site_va)
            if ok and hits:
                chosen = (pat, edit_off, len(hits))
                break
        if chosen is None:
            # no safe signature (dense, self-similar code) — keep an
            # address-pinned fallback so this build still applies 100%, and it
            # fails loud (expect-mismatch) rather than silently on a new build.
            stats["unsafe"] += 1
            unsafe.append((va, note))
            entries[(("addr", va), exp, rep)] = {
                "mode": "addr",
                "va": hex(va),
                "expect": exp.hex(),
                "replace": rep.hex(),
                "note": note,
            }
            continue
        pat, edit_off, count = chosen
        key = (pat, edit_off, exp, rep)
        if key not in entries:
            entries[key] = {
                "mode": "sig",
                "pattern": pat,
                "edit_offset": edit_off,
                "expect": exp.hex(),
                "replace": rep.hex(),
                "count": count,
                "note": note,
            }
    for e in entries.values():
        if e.get("mode") != "sig":
            continue
        if e["count"] == 1:
            stats["unique"] += 1
        else:
            stats["multi"] += 1
    return {
        "meta": {
            "source": str(src_dll),
            "image_base": hex(img.img),
            "edits_in": len(edits),
            "entries": len(entries),
            "stats": stats,
            "unsafe": [f"0x{va:08X} {n}" for va, n in unsafe],
        },
        "edits": list(entries.values()),
    }


# ── application ──────────────────────────────────────────────────────────────
def apply(target_dll: Path, sigpatch: dict, out: Path | None, dry_run: bool) -> dict:
    """Apply (or dry-run) a ``.sigpatch`` to ``target_dll`` — any build.

    Scans ``.text`` for each entry's signature and edits at the match; ``addr``
    fallback entries are located by ``va``. Locate-all-then-write, so co-located
    edits don't disturb each other. Idempotent (already-``replace`` sites count as
    ``already``); an ``expect`` mismatch or a vanished/non-unique signature is
    reported (``missing`` / ``ambiguous``), never written. With ``dry_run`` no
    file is written. Returns a report dict of counts and unresolved sites.
    """
    img = TextImage.load(target_dll)
    rep = {
        "entries": len(sigpatch["edits"]),
        "applied": 0,
        "already": 0,
        "missing": [],
        "ambiguous": [],
        "sites": 0,
    }
    rep["addr_fallback"] = 0
    writes = []  # (fileoff, replace_bytes)
    for e in sigpatch["edits"]:
        exp = bytes.fromhex(e["expect"])
        newb = bytes.fromhex(e["replace"])
        note = e.get("note", "")
        if e.get("mode") == "addr":
            # address-pinned fallback: locate by va, verify expect (fails loud)
            rep["addr_fallback"] += 1
            try:
                fo = img.va_to_fileoff(int(e["va"], 16))
            except Exception:
                rep["missing"].append((note, "addr:" + e["va"]))
                continue
            cur = bytes(img.data[fo : fo + len(exp)])
            if cur == newb:
                rep["already"] += 1
            elif cur == exp:
                writes.append((fo, newb))
            else:
                rep["missing"].append((note, "addr:" + e["va"] + " (expect-mismatch)"))
            continue
        hits = scan(img.text, e["pattern"])
        want = e["count"]
        if not hits:
            rep["missing"].append((note, e["pattern"]))
            continue
        if want == 1 and len(hits) != 1:
            rep["ambiguous"].append((note, len(hits), want))
            continue
        for h in hits:
            site_va = img.text_va + h + e["edit_offset"]
            fo = img.va_to_fileoff(site_va)
            cur = bytes(img.data[fo : fo + len(exp)])
            if cur == newb:
                rep["already"] += 1
                continue
            if cur != exp:
                rep["ambiguous"].append((note, "expect-mismatch@0x%08X" % site_va, want))
                continue
            writes.append((fo, newb))
    # locate-all-then-write
    for fo, newb in writes:
        img.data[fo : fo + len(newb)] = newb
    rep["applied"] = len(writes)
    rep["sites"] = len(writes) + rep["already"]
    if not dry_run and writes:
        (out or target_dll).write_bytes(bytes(img.data))
        rep["written"] = str(out or target_dll)
    return rep


def load_sigpatch(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def save_sigpatch(path: Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2))

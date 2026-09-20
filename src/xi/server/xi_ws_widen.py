"""``xi server ws-widen``: the C++ patch and SQL that let weapon skills carry animation
numbers above 255.

By default it is read-only for the server: it READS the three xi_map sources under
``XI_SERVER_DIR`` (to tell stock from widened and to diff against the user's own files)
and writes ``900-xitools-WsAnimation16.patch``, ``ws_animation_16bit.sql`` and a
``README.md`` into ``--out`` (default ``projects/patches/ws_animation_16bit/``), for the
user to apply.

``--install`` (:func:`ws_install`, the CatsEyeXI layout) does that local work in the
checkout instead: it copies the patch into ``modules/catseyexi/cpp-patches``, the SQL
into ``modules/catseyexi/sql/xi_tools``, and widens the three sources in place; with
``--apply-db`` it also widens the database column. It is idempotent and still never
builds, restarts or commits — the user rebuilds xi_map and restarts it afterwards.

It also holds the weapon-skill state ``xi server check`` reports (:func:`ws_state`):
the source edits, the database column, and whether xi_map was rebuilt with 16-bit
reads (the PDB's ``m_AnimationId`` member type, else file times).
"""
from __future__ import annotations

import difflib
import json
import mmap
import re
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click

from xi.server.xi_step import current_server_dir

SCHEMA = "xi.server-ws-widen.v1"

#: File order in the patch.
FILES = ("src/map/utils/battleutils.cpp", "src/map/weapon_skill.cpp", "src/map/weapon_skill.h")

# The four 8-bit hops (design2 §2.6.1, ws-widen.md §1). Each must match exactly once,
# in its stock or its wide form. Patterns are per line (re.M) and never cross a line:
# `[ \t]` rather than `\s`, which would let `^\s*` start on an earlier blank line.
_EDITS = (
    {"file": "src/map/utils/battleutils.cpp",
     "stock": r'setAnimationId\(rset->get<uint8>\("animation"\)\)',
     "wide": r'setAnimationId\(rset->get<uint16>\("animation"\)\)',
     "generic": ('PWeaponSkill->setAnimationId(rset->get<uint8>("animation"));',
                 'PWeaponSkill->setAnimationId(rset->get<uint16>("animation"));')},
    {"file": "src/map/weapon_skill.h",
     "stock": r'void setAnimationId\(uint8 id\);',
     "wide": r'void setAnimationId\(uint16 id\);',
     "generic": ("void setAnimationId(uint8 id);", "void setAnimationId(uint16 id);")},
    {"file": "src/map/weapon_skill.h",
     "stock": r'^[ \t]*uint8[ \t]+m_AnimationId;',
     "wide": r'^[ \t]*uint16[ \t]+m_AnimationId;',
     "generic": ("uint8  m_AnimationId;", "uint16 m_AnimationId;")},
    {"file": "src/map/weapon_skill.cpp",
     "stock": r'CWeaponSkill::setAnimationId\(const uint8 id\)',
     "wide": r'CWeaponSkill::setAnimationId\(const uint16 id\)',
     "generic": ("void CWeaponSkill::setAnimationId(const uint8 id)",
                 "void CWeaponSkill::setAnimationId(const uint16 id)")},
)

PATCH_NAME = "900-xitools-WsAnimation16.patch"
SQL_NAME = "ws_animation_16bit.sql"
README_NAME = "README.md"
DEFAULT_OUT = Path("projects") / "patches" / "ws_animation_16bit"

# ``--install`` (CatsEyeXI layout): where the patch and the module SQL go inside the
# server checkout. dbtool applies cpp-patches/*.patch (menu `a`), and imports module
# sql/ after the base sql/ on every update, so the column stays wide across a re-import.
CPP_PATCH_DIR = Path("modules") / "catseyexi" / "cpp-patches"
MODULE_SQL_DIR = Path("modules") / "catseyexi" / "sql" / "xi_tools"

ALTER = "ALTER TABLE weapon_skills MODIFY COLUMN animation smallint(5) unsigned NOT NULL DEFAULT '0'"

#: ws-widen.md §6.2, verbatim.
SQL_TEXT = (
    "-- Written by xi-tools `xi server ws-widen`. Safe to run again.\n"
    "-- Custom weapon-skill animations are numbered 272-527 (the cexislots plugin's third\n"
    "-- motion bank), which a tinyint can't hold. sql/weapon_skills.sql recreates this column\n"
    "-- as tinyint on every dbtool update; module SQL runs after it and widens it again.\n"
    "-- MODIFY keeps the column's position, so positional INSERTs (custom/items.sql) still work.\n"
    "-- Pairs with cpp-patches/900-xitools-WsAnimation16.patch (xi_map reads it as uint16).\n"
    f"{ALTER};\n"
)

#: The plain-language "why" (design2-overrides O4), shared with the README.
WHY = (
    "Custom weapon skills use animation numbers 264–527 (the cexislots plugin reads "
    "272–527). The server can't carry those yet: `weapon_skills.animation` only stores "
    "0–255, and xi_map reads it as an 8-bit number in four places in its C++. So a "
    "published weapon skill would play its number minus 256 — 300 plays as 44, a retail "
    "motion. The patch makes those four spots 16-bit and the SQL widens the column; "
    "retail weapon skills (all below 256) are unaffected. Apply the patch to your server "
    "checkout, run the SQL, rebuild xi_map and restart it."
)

WIDE_TYPES = frozenset({"smallint", "mediumint", "int", "integer", "bigint"})


# ── source files ──────────────────────────────────────────────────────────────

def _read(path: Path):
    """``(text, had_bom)`` of a UTF-8 source file, or ``None`` when it is missing or
    not UTF-8. EOLs are kept exactly (the three files are LF)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    bom = raw.startswith(b"\xef\xbb\xbf")
    if bom:
        raw = raw[3:]
    try:
        return raw.decode("utf-8"), bom
    except UnicodeDecodeError:
        return None


def _find(pattern: str, text: str) -> list:
    return list(re.finditer(pattern, text, re.M))


def _line_no(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:] if end < 0 else text[start:end]


def _widen_line(i: int, line: str) -> str:
    if i == 0:
        return line.replace('get<uint8>("animation")', 'get<uint16>("animation")', 1)
    if i == 1:
        return line.replace("setAnimationId(uint8 id);", "setAnimationId(uint16 id);", 1)
    if i == 2:
        # The member keeps its column: uint8 + N spaces -> uint16 + N-1 (N > 1).
        m = re.match(r"^([ \t]*)uint8([ \t]+)(m_AnimationId;.*)$", line, re.S)
        if not m:
            return line
        lead, gap, rest = m.groups()
        return f"{lead}uint16{gap[:-1] if len(gap) > 1 else gap}{rest}"
    return line.replace("setAnimationId(const uint8 id)", "setAnimationId(const uint16 id)", 1)


def _narrow_line(i: int, line: str) -> str:
    if i == 0:
        return line.replace('get<uint16>("animation")', 'get<uint8>("animation")', 1)
    if i == 1:
        return line.replace("setAnimationId(uint16 id);", "setAnimationId(uint8 id);", 1)
    if i == 2:
        m = re.match(r"^([ \t]*)uint16([ \t]+)(m_AnimationId;.*)$", line, re.S)
        if not m:
            return line
        lead, gap, rest = m.groups()
        return f"{lead}uint8{gap} {rest}"          # the inverse of _widen_line (N > 1)
    return line.replace("setAnimationId(const uint16 id)", "setAnimationId(const uint8 id)", 1)


def _apply(rel: str, text: str, wide: bool) -> str:
    """``text`` of file ``rel`` with its edits widened (``wide``) or narrowed. Only the
    matched lines change; every other byte, EOLs included, is kept."""
    for i, e in enumerate(_EDITS):
        if e["file"] != rel:
            continue
        hits = _find(e["stock"] if wide else e["wide"], text)
        for m in reversed(hits):
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.start())
            end = len(text) if end < 0 else end
            line = text[start:end]
            new = _widen_line(i, line) if wide else _narrow_line(i, line)
            text = text[:start] + new + text[end:]
    return text


def widen(rel: str, text: str) -> str:
    """The widened text of source file ``rel`` (a no-op on a line already wide)."""
    return _apply(rel, text, True)


def narrow(rel: str, text: str) -> str:
    """The stock text of source file ``rel``: reverses :func:`widen`, so the stock
    text can be derived from a widened tree."""
    return _apply(rel, text, False)


def _server_path(server_dir) -> Path | None:
    if not server_dir:
        return None
    p = Path(server_dir)
    return p if p.is_dir() else None


def _read_sources(server_dir) -> dict:
    """``{rel: (text, bom) | None}`` for the three files."""
    root = _server_path(server_dir)
    return {rel: (_read(root / rel) if root else None) for rel in FILES}


def source_state(server_dir=None, *, _files: dict | None = None) -> dict:
    """``{state, edits}`` for the server checkout at ``server_dir``.

    ``state``: ``stock`` (all 4 stock), ``widened`` (all 4 wide), ``partial`` (a mix of
    stock and wide), ``unrecognised`` (a line missing or matching more than once, or a
    file missing), ``no-server-dir`` (unset, or not a folder).

    ``edits[]``: ``{file, line, state, current, before, after}`` — ``state`` is
    ``stock|wide|unknown``; ``current`` the line as it is now (``None`` when not found
    once); ``before``/``after`` its stock and wide forms (the generic ones when the line
    isn't found)."""
    root = _server_path(server_dir)
    files = _files if _files is not None else _read_sources(server_dir)
    edits = []
    for i, e in enumerate(_EDITS):
        rec = {"file": e["file"], "line": None, "state": "unknown", "current": None,
               "before": e["generic"][0], "after": e["generic"][1]}
        got = files.get(e["file"]) if root else None
        if got:
            text = got[0]
            s, w = _find(e["stock"], text), _find(e["wide"], text)
            if len(s) == 1 and not w:
                m, rec["state"] = s[0], "stock"
            elif len(w) == 1 and not s:
                m, rec["state"] = w[0], "wide"
            else:
                m = (s or w or [None])[0]
            if m is not None:
                cur = _line_at(text, m.start())
                rec["line"] = _line_no(text, m.start())
                if rec["state"] != "unknown":
                    rec["current"] = cur
                    stock = cur if rec["state"] == "stock" else _narrow_line(i, cur)
                    rec["before"], rec["after"] = stock.strip(), _widen_line(i, stock).strip()
                else:
                    rec["current"] = cur
        edits.append(rec)
    if root is None:
        state = "no-server-dir"
    else:
        states = {e["state"] for e in edits}
        if "unknown" in states:
            state = "unrecognised"
        elif states == {"stock"}:
            state = "stock"
        elif states == {"wide"}:
            state = "widened"
        else:
            state = "partial"
    return {"state": state, "edits": edits}


def _diff(rel: str, a: str, b: str) -> str:
    out = [f"diff --git a/{rel} b/{rel}\n"]
    for line in difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                     f"a/{rel}", f"b/{rel}", n=3):
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def patch_text(server_dir=None, *, _files: dict | None = None, _state: dict | None = None) -> str | None:
    """The unified diff stock → wide of the user's own three files (UTF-8, LF, no BOM),
    or ``None`` when the tree is not ``stock`` or ``widened``. A widened tree is diffed
    from its :func:`narrow` text, so the patch always applies to a stock checkout."""
    files = _files if _files is not None else _read_sources(server_dir)
    st = _state or source_state(server_dir, _files=files)
    if st["state"] not in ("stock", "widened"):
        return None
    parts = []
    for rel in FILES:
        text = files[rel][0]
        stock = text if st["state"] == "stock" else narrow(rel, text)
        parts.append(_diff(rel, stock, widen(rel, stock)))
    return "".join(parts)


# ── binary / process / build ──────────────────────────────────────────────────

_PDB_CACHE: dict = {}
_LF_MEMBER = 0x150D
_PRIM = {0x20: "uint8", 0x21: "uint16"}


def pdb_member_type(pdb) -> str | None:
    """``uint8``/``uint16`` for ``m_AnimationId`` as the PDB's CodeView ``LF_MEMBER``
    (0x150D) records type it (0x20 unsigned char, 0x21 unsigned short), read through a
    read-only mmap. Only ``CWeaponSkill`` declares this member, so a real PDB usually
    holds the current record plus **stale** ones the linker kept from earlier incremental
    builds; when it holds both an 8- and a 16-bit record, the widen is what this build
    compiled, so ``uint16`` wins. ``None`` when no ``uint8``/``uint16`` record is found or
    the file can't be read (a record straddling an MSF page is missed: that is ``None`` too)."""
    p = Path(pdb)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key in _PDB_CACHE:
        return _PDB_CACHE[key]
    found = set()
    needle = b"m_AnimationId\x00"
    try:
        with open(p, "rb") as fh:
            if st.st_size == 0:
                return None
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                at = mm.find(needle)
                while at != -1:
                    if at >= 10:
                        leaf, _attr, typ, _off = struct.unpack("<HHIH", mm[at - 10:at])
                        if leaf == _LF_MEMBER:
                            found.add(_PRIM.get(typ, f"0x{typ:X}"))
                    at = mm.find(needle, at + 1)
    except (OSError, ValueError):
        return None
    # A 16-bit record means this build compiled the widened member; a lingering 8-bit one
    # is a stale incremental-build record. Only a bare 8-bit (no 16-bit) reads as uint8.
    out = "uint16" if "uint16" in found else ("uint8" if "uint8" in found else None)
    _PDB_CACHE[key] = out
    return out


def _mtime(p: Path):
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _exe(root: Path) -> Path | None:
    for name in ("xi_map.exe", "xi_map"):
        if (root / name).is_file():
            return root / name
    return None


def binary_state(server_dir=None, source: str | None = None, *, probe: bool = True) -> dict:
    """``{exe, exeTime, pdb, pdbMatchesExe, memberType, method, rebuilt}``.

    ``rebuilt``: from the PDB, ``memberType == "uint16"`` (method ``pdb``); without a
    readable PDB record (method ``mtime``), with the source ``widened``, whether the exe
    is newer than all three sources; with the source ``stock``, ``False``; otherwise
    ``None``. No exe: method ``none``. ``probe=False``: method ``skipped``, all else null."""
    blank = {"exe": None, "exeTime": None, "pdb": None, "pdbMatchesExe": None,
             "memberType": None, "method": "skipped" if not probe else "none", "rebuilt": None}
    if not probe:
        return blank
    root = _server_path(server_dir)
    if root is None:
        return blank
    source = source or source_state(root)["state"]
    exe = _exe(root)
    if exe is None:
        return blank
    out = dict(blank)
    exe_t = _mtime(exe)
    out["exe"] = exe.name
    out["exeTime"] = datetime.fromtimestamp(exe_t).strftime("%Y-%m-%dT%H:%M:%S") if exe_t else None
    pdb = root / "xi_map.pdb"
    if pdb.is_file():
        out["pdb"] = pdb.name
        pdb_t = _mtime(pdb)
        out["pdbMatchesExe"] = (exe_t is not None and pdb_t is not None and abs(exe_t - pdb_t) < 60)
        mt = pdb_member_type(pdb)
        if mt:
            out["memberType"] = mt
            out["method"] = "pdb"
            out["rebuilt"] = mt == "uint16"
            return out
    out["method"] = "mtime"
    if source == "widened":
        srcs = [_mtime(root / rel) for rel in FILES]
        out["rebuilt"] = (None if exe_t is None or any(t is None for t in srcs)
                          else exe_t > max(srcs))
    elif source == "stock":
        out["rebuilt"] = False
    return out


def process_running(name: str = "xi_map.exe") -> bool | None:
    """Whether ``name`` is running (``tasklist``, as ``xi server status``); ``None``
    when the process list can't be read."""
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.strip().strip('"').split('","')
        if parts and parts[0].lower() == name.lower():
            return True
    return False


def build_command(server_dir=None) -> str | None:
    """``cmake --build <dir>/build/<cfg> --target xi_map`` from the first
    ``build/*/CMakeCache.txt``; ``None`` when there is none."""
    root = _server_path(server_dir)
    if root is None:
        return None
    try:
        caches = sorted((root / "build").glob("*/CMakeCache.txt"))
    except OSError:
        return None
    return f"cmake --build {caches[0].parent} --target xi_map" if caches else None


def ws_state(conn=None, server_dir=None, *, binary: bool = True) -> dict:
    """The weapon-skill state ``xi server check`` reports (design2 §1.3.3, as O4 changed
    it): ``{column, columnWide, source{state, edits}, binary, process, buildCommand,
    ready, next}``. ``conn`` (read-only: one information_schema SELECT) gives the column;
    without it ``column``/``columnWide`` are ``None``. ``binary=False`` skips the PDB
    probe and the process check."""
    server_dir = server_dir if server_dir is not None else current_server_dir()
    column = data_type = None
    if conn is not None:
        from xi.server.xi_db_apply import _q
        rows = _q(conn, "SELECT DATA_TYPE, COLUMN_TYPE FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                  ("weapon_skills", "animation"))
        if rows:
            data_type, column = str(rows[0][0]).lower(), str(rows[0][1])
    wide = (data_type in WIDE_TYPES) if column is not None else None
    src = source_state(server_dir)
    bin_ = binary_state(server_dir, src["state"], probe=binary)
    ready = wide is True and src["state"] == "widened" and bin_["rebuilt"] is True
    if src["state"] != "widened":
        nxt = "patch"
    elif wide is False:
        nxt = "alter"
    elif bin_["rebuilt"] is False:
        nxt = "rebuild"
    else:
        nxt = "none"
    return {
        "column": column,
        "columnWide": wide,
        "source": {"state": src["state"], "edits": src["edits"]},
        "binary": bin_,
        "process": {"running": process_running() if binary and _server_path(server_dir) else None},
        "buildCommand": build_command(server_dir),
        "ready": ready,
        "next": [nxt],
    }


# ── the command ───────────────────────────────────────────────────────────────

def readme_text(server_dir=None, build_cmd: str | None = None, patch_path=None) -> str:
    """The README written beside the patch: the why, then how to apply and check."""
    sd = str(server_dir) if server_dir else "<your server checkout>"
    patch = str(patch_path) if patch_path else PATCH_NAME
    build = build_cmd or "cmake --build <your build folder> --target xi_map"
    return (
        "# Weapon-skill animations above 255\n"
        "\n"
        "Written by xi-tools (`xi server ws-widen`). Nothing here has been applied: xi-tools\n"
        "never edits your server's source or its database, and never builds or restarts it.\n"
        "\n"
        "## Why\n"
        "\n"
        f"{WHY}\n"
        "\n"
        "## What's here\n"
        "\n"
        f"- `{PATCH_NAME}` — the C++ change: four 8-bit spots in `src/map/utils/battleutils.cpp`,\n"
        "  `src/map/weapon_skill.h` and `src/map/weapon_skill.cpp` become 16-bit.\n"
        f"- `{SQL_NAME}` — widens `weapon_skills.animation` to `smallint(5) unsigned`. Safe to run again.\n"
        "\n"
        "## How to apply\n"
        "\n"
        "1. Apply the patch to your server checkout:\n"
        "\n"
        f"       git -C \"{sd}\" apply \"{patch}\"\n"
        "\n"
        "   Or, if your team keeps C++ patches in a module (CatsEyeXI: `modules/catseyexi/cpp-patches/`),\n"
        "   copy the patch there and let dbtool's *Apply Patches* apply it.\n"
        "2. Run the SQL against the server's database with your usual MySQL/MariaDB client — or put it\n"
        "   in a module's `sql/` folder and let dbtool run it. A dbtool update re-creates the column as\n"
        "   0–255 from `sql/weapon_skills.sql`; module SQL runs after it and widens it again.\n"
        "3. Rebuild xi_map:\n"
        "\n"
        f"       {build}\n"
        "\n"
        "4. Restart xi_map.\n"
        "\n"
        "## How to check\n"
        "\n"
        "    xi server check\n"
        "\n"
        "Its `weapon skills` line reads `ready (16-bit)` once the source is patched, the column is\n"
        "wide and xi_map has been rebuilt (the model viewer shows the same in Settings › Local Server ›\n"
        "Weapon skills). Until then, publishing a weapon skill with an animation above 255 leaves the\n"
        "database alone.\n"
    )


def _put(path: Path, text: str) -> str:
    """Write ``text`` (UTF-8, LF) only when it differs: ``written`` or ``already``."""
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return "already"
    except OSError:
        pass
    path.write_bytes(data)
    return "written"


def _target_lines(edits: list, *, generic: bool = False) -> list:
    """``file:line  <current text>`` per edit, for a hand edit. ``generic`` (no server
    folder to read): ``file:?  <the stock line as upstream has it>``."""
    out = []
    for e in edits:
        if generic:
            out.append(f"             {e['file']}:?  {e['before']}")
            continue
        where = f"{e['file']}:{e['line']}" if e.get("line") else f"{e['file']}:?"
        out.append(f"             {where}  {(e.get('current') or '(not found)').strip()}")
    return out


_STATE_TEXT = {
    "stock": "stock — xi_map reads weapon-skill animations as 8 bits (the 4 lines below)",
    "widened": "widened — your checkout already has the change; the patch is derived back to stock",
    "partial": "partly widened — refused; finish the 4 lines by hand",
    "unrecognised": "unrecognised — a line is missing or appears twice (upstream moved it?); refused",
    "no-server-dir": "no server folder (set XI_SERVER_DIR: Settings › Local Server in the model viewer)",
}


def _missing_dir_text(server_dir) -> str:
    """The source line when ``XI_SERVER_DIR`` is set but isn't a folder (a typo, a moved
    checkout): it names the path instead of asking for it to be set."""
    return f"server folder not found: {server_dir} (fix XI_SERVER_DIR: Settings › Local Server in the model viewer)"


def ws_widen(out_dir=None, *, print_only: bool = False, server_dir=None) -> dict:
    """Build ``xi.server-ws-widen.v1`` and, unless ``print_only``, write the patch, the
    SQL and the README into ``out_dir`` (default ``projects/patches/ws_animation_16bit``
    under the working folder). Files are rewritten only when their content differs.
    Never touches the server checkout or a database."""
    server_dir = server_dir if server_dir is not None else current_server_dir()
    root = _server_path(server_dir)
    out = Path(out_dir) if out_dir else Path.cwd() / DEFAULT_OUT
    out = out.resolve()
    files = _read_sources(server_dir)
    src = source_state(server_dir, _files=files)
    ptext = patch_text(server_dir, _files=files, _state=src)
    bcmd = build_command(server_dir)
    ppath, spath, rpath = out / PATCH_NAME, out / SQL_NAME, out / README_NAME
    rtext = readme_text(root, bcmd, ppath)
    doc = {
        "schema": SCHEMA, "ok": True, "mode": "print" if print_only else "write",
        "serverDir": str(root) if root else (str(Path(server_dir)) if server_dir else None),
        "outDir": str(out),
        "source": {"state": src["state"], "edits": src["edits"]},
        "patch": {"path": str(ppath), "action": None, "text": None},
        "sql": {"path": str(spath), "action": None, "text": None},
        "readme": {"path": str(rpath), "action": None, "text": None},
        "buildCommand": bcmd, "lines": [], "error": None,
    }
    # XI_SERVER_DIR set, but not a folder: say so, rather than "set XI_SERVER_DIR".
    missing = src["state"] == "no-server-dir" and bool(str(server_dir or "").strip())
    why_refused = None
    if ptext is None:
        why_refused = {"no-server-dir": "no server folder to read the sources from",
                       "partial": "the sources are partly widened",
                       "unrecognised": "the sources don't match the expected lines"}.get(src["state"], "not derivable")
        if missing:
            why_refused = f"server folder not found: {server_dir}"
    try:
        if print_only:
            doc["patch"].update(action="refused" if ptext is None else "printed", text=ptext)
            doc["sql"].update(action="printed", text=SQL_TEXT)
            doc["readme"].update(action="printed", text=rtext)
        else:
            out.mkdir(parents=True, exist_ok=True)
            doc["patch"]["action"] = "refused" if ptext is None else _put(ppath, ptext)
            doc["sql"]["action"] = _put(spath, SQL_TEXT)
            doc["readme"]["action"] = _put(rpath, rtext)
    except OSError as e:
        doc["error"] = f"couldn't write {out}: {e}"
    doc["ok"] = doc["error"] is None and "refused" not in (
        doc["patch"]["action"], doc["sql"]["action"], doc["readme"]["action"])

    lines = [f"ws-widen: {doc['serverDir'] or '(no server folder)'}",
             f"  source:  {_missing_dir_text(server_dir) if missing else _STATE_TEXT[src['state']]}"]
    # The 4 lines to edit, always (design2-overrides O4): with no folder to read, the
    # stock text upstream has at each (the line numbers are unknown).
    lines += _target_lines(src["edits"], generic=src["state"] == "no-server-dir")
    act = lambda d, extra="": f"{d['path']} ({d['action']}{extra})"  # noqa: E731
    if print_only:
        lines.append("  (printed only; nothing written)")
    else:
        lines.append(f"  patch:   {act(doc['patch'], f' — {why_refused}' if ptext is None else '')}")
        lines.append(f"  sql:     {act(doc['sql'])}")
        lines.append(f"  readme:  {act(doc['readme'])}")
    if doc["error"]:
        lines.append(f"  error:   {doc['error']}")
    elif ptext is not None and not print_only:
        lines.append(f"next: apply the patch (git -C \"{root}\" apply \"{ppath}\"), run the SQL, "
                     f"rebuild xi_map{f' ({bcmd})' if bcmd else ''} and restart it — see {rpath}")
    doc["lines"] = lines
    return doc


# ── --install: place the patch and widen the source in the server checkout ──────

def _widen_source(root: Path, files: dict, src_state: str) -> tuple[str, str | None]:
    """Widen the three sources in place (the same change the patch makes), keeping every
    other byte, the EOLs and each BOM. ``(action, error)``: ``applied`` (≥1 file edited),
    ``already`` (all four lines already wide), ``refused`` (partial/unrecognised — left
    untouched), ``error`` (a write failed)."""
    if src_state == "widened":
        return "already", None
    if src_state != "stock":
        return "refused", None
    changed = False
    try:
        for rel in FILES:
            got = files.get(rel)
            if not got:
                return "refused", None
            text, bom = got
            new = widen(rel, text)
            if new != text:
                (root / rel).write_bytes((b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8"))
                changed = True
    except OSError as e:
        return "error", str(e)
    return ("applied" if changed else "already"), None


def _install_db(db_rec: dict, *, conn=None) -> None:
    """Widen ``weapon_skills.animation`` to smallint when it is still tinyint. Fills
    ``db_rec`` with ``action`` (``altered``/``already-wide``/``skipped``/``refused``/
    ``error``), the ``column`` type and, on an ALTER, the ``after`` type. Safe before the
    rebuild: every stock value is ≤ 243, and nothing reads the column at runtime."""
    from xi.server import xi_db_apply as A
    close = False
    if conn is None:
        if A.configured() is None:
            db_rec["action"], db_rec["error"] = "skipped", "no database configured"
            return
        try:
            conn = A.connect()
        except A.DbUnavailable as e:
            db_rec["action"], db_rec["error"] = "error", str(e)
            return
        close = True
    sel = ("SELECT DATA_TYPE, COLUMN_TYPE FROM information_schema.COLUMNS "
           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s")
    try:
        rows = A._q(conn, sel, ("weapon_skills", "animation"))
        if not rows:
            db_rec["action"], db_rec["error"] = "refused", "no weapon_skills.animation column"
            return
        data_type, db_rec["column"] = str(rows[0][0]).lower(), str(rows[0][1])
        if data_type in WIDE_TYPES:
            db_rec["action"] = "already-wide"
            return
        A._q(conn, ALTER, (), write=True)
        after = A._q(conn, sel, ("weapon_skills", "animation"))
        db_rec["after"] = str(after[0][1]) if after else None
        db_rec["action"] = "altered"
    except Exception as e:                           # noqa: BLE001 — any DB failure is "error"
        db_rec["action"], db_rec["error"] = "error", A.friendly_db_error(e)
    finally:
        if close:
            try:
                conn.close()
            except Exception:                        # noqa: BLE001
                pass


_SRC_APPLY_TEXT = {
    "applied": "applied — the 4 lines are now 16-bit in your source",
    "already": "already 16-bit in your source",
    "refused": "refused — the source is partly widened or doesn't match; left as it is (Show the code has the 4 lines)",
    "error": "couldn't edit the source",
}
_DB_APPLY_TEXT = {
    "altered": "column widened",
    "already-wide": "already 16-bit",
    "skipped": "not run — no database set; module SQL applies it on the next dbtool update",
    "refused": "no weapon_skills.animation column",
    "error": "couldn't widen the column",
}


def ws_install(server_dir=None, *, apply_db: bool = False, _conn=None) -> dict:
    """Place ``900-xitools-WsAnimation16.patch`` and ``ws_animation_16bit.sql`` in the
    server's ``modules/catseyexi`` and widen xi_map's three sources in place, so a rebuild
    picks the change up. With ``apply_db`` it also widens the database column. It never
    builds, restarts or commits: the user rebuilds xi_map and restarts it afterwards.

    Returns ``xi.server-ws-widen.v1`` with ``mode: "install"`` and an ``install`` block.
    It is idempotent — a second run reports ``already`` for every step."""
    server_dir = server_dir if server_dir is not None else current_server_dir()
    root = _server_path(server_dir)
    files = _read_sources(server_dir)
    src = source_state(server_dir, _files=files)
    ptext = patch_text(server_dir, _files=files, _state=src)
    bcmd = build_command(server_dir)
    cpp_path = (root / CPP_PATCH_DIR / PATCH_NAME) if root else None
    sql_path = (root / MODULE_SQL_DIR / SQL_NAME) if root else None
    inst = {
        "cppPatch": {"path": str(cpp_path) if cpp_path else None, "action": None},
        "moduleSql": {"path": str(sql_path) if sql_path else None, "action": None},
        "sourceApply": {"action": None, "error": None},
        "db": {"action": None, "column": None, "after": None, "error": None},
    }
    doc = {
        "schema": SCHEMA, "ok": True, "mode": "install",
        "serverDir": str(root) if root else (str(Path(server_dir)) if server_dir else None),
        "source": {"state": src["state"], "edits": src["edits"]},
        "install": inst, "buildCommand": bcmd, "lines": [], "next": [], "error": None,
    }
    if root is None:
        doc["ok"] = False
        doc["error"] = (f"server folder not found: {server_dir}" if str(server_dir or "").strip()
                        else "no server folder (set XI_SERVER_DIR: Settings › Local Server in the model viewer)")
        doc["lines"] = [f"ws-widen install: {doc['error']}"]
        return doc

    # 1-2. Place the patch and the SQL into modules/catseyexi (the CatsEyeXI house way).
    # A partly-widened or unrecognised tree has no derivable patch: refuse and write nothing.
    if ptext is None:
        inst["cppPatch"]["action"] = inst["moduleSql"]["action"] = "refused"
    else:
        try:
            cpp_dir = root / CPP_PATCH_DIR
            inst["cppPatch"]["action"] = _put(cpp_path, ptext) if cpp_dir.is_dir() else "skipped"
            if (root / "modules" / "catseyexi" / "sql").is_dir():
                (root / MODULE_SQL_DIR).mkdir(parents=True, exist_ok=True)
                inst["moduleSql"]["action"] = _put(sql_path, SQL_TEXT)
            else:
                inst["moduleSql"]["action"] = "skipped"
        except OSError as e:
            doc["error"] = f"couldn't write into modules/catseyexi: {e}"

    # 3. Widen the three sources in place, so a rebuild carries the change.
    inst["sourceApply"]["action"], inst["sourceApply"]["error"] = _widen_source(root, files, src["state"])

    # 4. The database column (only with --apply-db).
    if apply_db:
        _install_db(inst["db"], conn=_conn)
    else:
        inst["db"]["action"] = "skipped"

    bad = {"refused", "error"}
    doc["ok"] = (doc["error"] is None
                 and inst["sourceApply"]["action"] not in bad
                 and inst["cppPatch"]["action"] not in bad
                 and inst["db"]["action"] not in bad)

    # What is left for the user: run the SQL if the DB wasn't touched, then rebuild+restart.
    nxt: list[str] = []
    if inst["sourceApply"]["action"] in ("applied", "already"):
        db_done = apply_db and inst["db"]["action"] in ("altered", "already-wide")
        if not db_done:                              # the column still needs the SQL (or dbtool)
            nxt.append("sql")
        nxt += ["rebuild", "restart"]
    doc["next"] = nxt or (["fix-source"] if inst["sourceApply"]["action"] in bad else ["done"])

    lines = [f"ws-widen install: {doc['serverDir']}",
             f"  source:  {_SRC_APPLY_TEXT.get(inst['sourceApply']['action'], inst['sourceApply']['action'])}"
             + (f" ({inst['sourceApply']['error']})" if inst["sourceApply"]["error"] else "")]
    _put_note = {"written": "written", "already": "already there", "skipped": "no module folder — skipped",
                 "refused": "refused — the patch can't be derived from this source"}
    lines.append(f"  patch:   {inst['cppPatch']['path'] or '(no server folder)'} "
                 f"({_put_note.get(inst['cppPatch']['action'], inst['cppPatch']['action'])})")
    lines.append(f"  sql:     {inst['moduleSql']['path'] or '(no module folder)'} "
                 f"({_put_note.get(inst['moduleSql']['action'], inst['moduleSql']['action'])})")
    if apply_db:
        db = inst["db"]
        extra = ""
        if db["action"] == "altered":
            extra = f" {db['column']} → {db['after']}"
        elif db["action"] == "error":
            extra = f" ({db['error']})"
        lines.append(f"  db:      {_DB_APPLY_TEXT.get(db['action'], db['action'])}{extra}")
    if doc["error"]:
        lines.append(f"  error:   {doc['error']}")
    if "rebuild" in doc["next"]:
        sql_left = " run the SQL (ws_animation_16bit.sql), then" if "sql" in doc["next"] else ""
        lines.append(f"next:{sql_left} rebuild xi_map{f' ({bcmd})' if bcmd else ''} and restart it")
    doc["lines"] = lines
    return doc


# ── the command ─────────────────────────────────────────────────────────────── (part 2)

@click.command("ws-widen")
@click.option("--json", "as_json", is_flag=True, help="Print xi.server-ws-widen.v1 JSON; always exits 0.")
@click.option("--print", "print_only", is_flag=True,
              help="Write nothing: return the patch, SQL and README text (Show the code).")
@click.option("--install", "install", is_flag=True,
              help="Place the patch in modules/catseyexi and widen xi_map's source in place (CatsEyeXI).")
@click.option("--apply-db", "apply_db", is_flag=True,
              help="With --install: also widen the weapon_skills.animation column in the database.")
@click.option("--out", "out_dir", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Folder to write into  [default: projects/patches/ws_animation_16bit]")
def ws_widen_cmd(as_json, print_only, install, apply_db, out_dir):
    """Let weapon skills use animation numbers above 255 (the C++ patch + SQL).

    By default this is read-only for your server: it reads xi_map's three sources under
    XI_SERVER_DIR (never edits them) and writes 900-xitools-WsAnimation16.patch,
    ws_animation_16bit.sql and a README into --out, for you to apply.

    --install (CatsEyeXI layout) does the local work for you: it copies the patch into
    modules/catseyexi/cpp-patches, the SQL into modules/catseyexi/sql/xi_tools, and
    widens the three sources in place. --apply-db also widens the database column. It is
    idempotent and still never builds, restarts or commits — rebuild xi_map and restart
    it afterwards.

    \b
    Examples:
      xi server ws-widen
      xi server ws-widen --print
      xi server ws-widen --install --apply-db
      xi server ws-widen --json --out D:\\tmp\\ws
    """
    doc = ws_install(apply_db=apply_db) if install else ws_widen(out_dir, print_only=print_only)
    if as_json:
        click.echo(json.dumps(doc, ensure_ascii=False, indent=2))
        return
    for line in doc["lines"]:
        click.echo(line)
    if print_only:
        click.echo()
        click.echo(doc["patch"]["text"] or "(no patch: the sources can't be read as stock or widened)")
        click.echo(doc["sql"]["text"])
    if not doc["ok"]:
        sys.exit(1)

"""Bundled ``npc_list`` snapshot — NPC appearance/placement without a live server.

The editor resolves a cutscene actor's model from its ``npc_list`` row (``look`` →
:func:`xi.gear.xi_core.parse_look` → GLB). That table lives in the *server* database,
so anyone without a local LSB/CatsEyeXI instance running gets no NPCs at all in the
cutscene preview — the DB error is swallowed and every actor comes back
``hasModel: False``.

This module ships a read-only snapshot of the six columns that actually matter
(``npcid, name, look, pos_x, pos_y, pos_z, pos_rot``) so the preview works out of the
box. A reachable database always wins per-id; the snapshot only fills ids the DB did
not answer (see :func:`xi.zone.xi_bridge._npc_look_rows`).

Build it with ``xi server npc-snapshot``, which parses the server checkout's
``sql/npc_list.sql`` mysqldump — no running database required.

The file is gzipped JSON: readable with ``gzip -dc npc_list.json.gz | jq``, and cheap
to hand-edit if you ever need to. Gzip is purely a size concern (~460 KiB instead of
~3.4 MiB); nothing else depends on it::

    {
      "format": "xi.npc_list.v1",
      "meta": {"source": "…", "generated": "…", "table": "npc_list", "rows": 31402},
      "npcs": {
        "16781329": {"name": "NPC[11]", "look": "00003200…", "pos": [0, 0, 0], "rot": 0}
      }
    }
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

FORMAT = "xi.npc_list.v1"

#: Columns the editor needs. Everything else in ``npc_list`` is server behaviour.
COLUMNS = ("npcid", "name", "look", "pos_x", "pos_y", "pos_z", "pos_rot")

#: Column order used when a dump carries no ``CREATE TABLE`` block to read.
_FALLBACK_COLUMNS = (
    "npcid", "name", "polutils_name", "pos_rot", "pos_x", "pos_y", "pos_z",
    "flag", "speed", "speedsub", "animation", "animationsub", "namevis",
    "status", "entityFlags", "look", "name_prefix", "content_tag", "widescan",
)


def default_path() -> Path:
    """Where the bundled snapshot lives inside the package (ships with ``src/``)."""
    return Path(__file__).resolve().parent / "data" / "npc_list.json.gz"


# ── Reading ───────────────────────────────────────────────────────────────────

class Snapshot:
    """A parsed snapshot: ``meta`` plus an ``npcid → row`` table.

    Rows are kept as their JSON form and converted on lookup — only ``look`` needs
    work (hex → bytes), and a cutscene touches a handful of the ~31k rows."""

    def __init__(self, meta: dict, npcs: dict):
        self.meta = meta
        self._npcs = npcs

    def __len__(self) -> int:
        return len(self._npcs)

    def __contains__(self, npcid: int) -> bool:
        return str(npcid) in self._npcs

    def row(self, npcid: int) -> dict | None:
        """One row in :func:`xi.zone.xi_bridge._npc_look_rows` shape, or ``None``."""
        rec = self._npcs.get(str(npcid))
        if rec is None:
            return None
        try:
            look = bytes.fromhex(rec.get("look") or "")
        except ValueError:
            look = b""
        pos = rec.get("pos") or [0, 0, 0]
        return {
            "name": rec.get("name") or "",
            "look": look.ljust(20, b"\0")[:20],
            "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
            "rot": int(rec.get("rot") or 0),
        }

    def rows(self, ids) -> dict:
        """``{npcid: row}`` for the ids present in the snapshot; missing ids are skipped."""
        out: dict = {}
        for i in ids:
            try:
                nid = int(i)
            except (TypeError, ValueError):
                continue
            row = self.row(nid)
            if row is not None:
                out[nid] = row
        return out


def parse(data: bytes) -> Snapshot:
    """Parse snapshot bytes (gzipped or plain JSON) into a :class:`Snapshot`."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    obj = json.loads(data.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("npc snapshot is not a JSON object")
    fmt = obj.get("format")
    if fmt != FORMAT:
        raise ValueError(f"npc snapshot format {fmt!r} — this build reads {FORMAT!r}")
    npcs = obj.get("npcs")
    if not isinstance(npcs, dict):
        raise ValueError("npc snapshot has no 'npcs' table")
    return Snapshot(obj.get("meta") or {}, npcs)


# Parsed once per process; ``False`` means "tried and unavailable", so a missing or
# corrupt file costs one failed load rather than one per lookup.
_CACHE: object = None


def load(path: Path | None = None) -> Snapshot | None:
    """Load the bundled snapshot, or ``None`` when it is absent/unreadable.

    Never raises — the snapshot is a convenience layer, and a broken one must not take
    down a bridge call that would otherwise have worked off the live database."""
    global _CACHE
    if path is not None:
        try:
            return parse(Path(path).read_bytes())
        except (OSError, ValueError, EOFError, UnicodeDecodeError, gzip.BadGzipFile):
            return None
    if _CACHE is not None:
        return _CACHE or None
    try:
        snap = parse(default_path().read_bytes())
    except (OSError, ValueError, EOFError, UnicodeDecodeError, gzip.BadGzipFile):
        _CACHE = False
        return None
    _CACHE = snap
    return snap


def rows(ids, path: Path | None = None) -> dict:
    """``{npcid: {name, look, pos, rot}}`` from the bundled snapshot. ``{}`` if absent."""
    snap = load(path)
    return snap.rows(ids) if snap is not None else {}


# ── Building ──────────────────────────────────────────────────────────────────

_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+`?npc_list`?\s*\((.*?)\)\s*ENGINE", re.IGNORECASE | re.DOTALL)
_COL_RE = re.compile(r"^\s*`([A-Za-z0-9_]+)`\s+", re.MULTILINE)
_INSERT_RE = re.compile(
    r"INSERT\s+(?:LOW_PRIORITY\s+|DELAYED\s+|HIGH_PRIORITY\s+)?(?:IGNORE\s+)?"
    r"INTO\s+`?npc_list`?\s*(?:\([^)]*\)\s*)?VALUES\s*", re.IGNORECASE)
#: ``_binary 'xx'`` / ``_utf8mb4 'xx'`` charset introducers before a quoted literal.
_INTRODUCER_RE = re.compile(r"_[A-Za-z0-9]+\s+(?=['\"])")

_ESCAPES = {
    "0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t",
    "Z": "\x1a", "\\": "\\", "'": "'", '"': '"',
}


def _columns_from_dump(text: str) -> tuple:
    """Column order from the dump's own ``CREATE TABLE``, else the known stock order.

    Reading the schema out of the file keeps the parser correct if upstream ever
    reorders or adds columns, instead of silently mapping values to the wrong fields."""
    m = _CREATE_RE.search(text)
    if not m:
        return _FALLBACK_COLUMNS
    cols = tuple(_COL_RE.findall(m.group(1)))
    return cols or _FALLBACK_COLUMNS


def _read_value(text: str, i: int):
    """Read one SQL value literal at ``i``; returns ``(value, next_index)``.

    Quoted literals come back as ``str`` (the dump is read as latin-1, so each char is
    one source byte and ``.encode('latin-1')`` recovers the exact bytes). Bare ``0x…``
    comes back as ``bytes``, ``NULL`` as ``None``, everything else as ``str``."""
    n = len(text)
    m = _INTRODUCER_RE.match(text, i)
    if m:
        i = m.end()
    if i < n and text[i] in "'\"":
        quote = text[i]
        i += 1
        buf: list = []
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                buf.append(_ESCAPES.get(text[i + 1], text[i + 1]))
                i += 2
                continue
            if ch == quote:
                if i + 1 < n and text[i + 1] == quote:   # '' → escaped quote
                    buf.append(quote)
                    i += 2
                    continue
                i += 1
                break
            buf.append(ch)
            i += 1
        return "".join(buf), i

    j = i
    while j < n and text[j] not in ",)":
        j += 1
    s = text[i:j].strip()
    if not s or s.upper() == "NULL":
        return None, j
    if s[:2].lower() == "0x":
        h = s[2:]
        # MySQL left-pads an odd-length hex literal with a zero nibble, and upstream
        # npc_list has at least one such row (npcid 17072359, 41 digits). Matching that
        # keeps the snapshot byte-identical to what the server would have loaded.
        if len(h) % 2:
            h = "0" + h
        try:
            return bytes.fromhex(h), j
        except ValueError:
            return None, j
    return s, j


def _split_values(text: str, start: int) -> tuple[list, int]:
    """Parse the ``(…),(…);`` tuple list of one INSERT starting at ``start``.

    Hand-rolled rather than regex: values contain quoted strings with escaped quotes
    and embedded commas, which a regex split would shred."""
    out: list = []
    i, n = start, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != "(":
            break
        i += 1
        row: list = []
        while i < n:
            while i < n and text[i].isspace():
                i += 1
            if i < n and text[i] == ")":
                i += 1
                break
            val, i = _read_value(text, i)
            row.append(val)
            while i < n and text[i].isspace():
                i += 1
            if i < n and text[i] == ",":
                i += 1
                continue
            if i < n and text[i] == ")":
                i += 1
                break
        out.append(row)
        while i < n and text[i].isspace():
            i += 1
        if i < n and text[i] == ",":       # multi-row INSERT … VALUES (…),(…)
            i += 1
            continue
        if i < n and text[i] == ";":
            i += 1
        break
    return out, i


def _as_bytes(v) -> bytes:
    """Recover raw column bytes from a parsed value (latin-1 round-trip for literals)."""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if v is None:
        return b""
    return str(v).encode("latin-1", "replace")


def _as_float(v) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def _as_int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def _is_commented_out(text: str, pos: int) -> bool:
    """True when the statement at ``pos`` sits behind a ``--`` / ``#`` line comment.

    Upstream ``npc_list.sql`` keeps superseded rows commented out directly under the
    live one, and those stale copies do not always carry the same column layout — so
    parsing them silently overwrites good rows with column-shifted garbage."""
    line_start = text.rfind("\n", 0, pos) + 1
    head = text[line_start:pos]
    return "--" in head or "#" in head


def parse_dump(text: str, stats: dict | None = None) -> list[dict]:
    """Extract ``npc_list`` rows from a mysqldump, keeping only :data:`COLUMNS`.

    ``text`` must have been read as latin-1 so binary columns survive byte-exact.
    ``stats``, if given, collects ``commented`` / ``malformed`` / ``duplicate`` counts."""
    cols = _columns_from_dump(text)
    idx = {name: i for i, name in enumerate(cols)}
    missing = [c for c in COLUMNS if c not in idx]
    if missing:
        raise ValueError(f"dump is missing column(s): {', '.join(missing)}")
    width = len(cols)
    counts = {"commented": 0, "malformed": 0, "duplicate": 0}

    seen: dict = {}
    out: list[dict] = []
    for m in _INSERT_RE.finditer(text):
        if _is_commented_out(text, m.start()):
            counts["commented"] += 1
            continue
        tuples, _end = _split_values(text, m.end())
        for vals in tuples:
            # Exact width only. A short/long tuple means a different column layout, and
            # mapping it positionally would shift every field after the missing one.
            if len(vals) != width:
                counts["malformed"] += 1
                continue
            try:
                npcid = int(str(vals[idx["npcid"]]).strip())
            except (TypeError, ValueError):
                counts["malformed"] += 1
                continue
            row = {
                "npcid": npcid,
                "name": _as_bytes(vals[idx["name"]]),
                "look": _as_bytes(vals[idx["look"]]),
                "pos_x": _as_float(vals[idx["pos_x"]]),
                "pos_y": _as_float(vals[idx["pos_y"]]),
                "pos_z": _as_float(vals[idx["pos_z"]]),
                "pos_rot": _as_int(vals[idx["pos_rot"]]),
            }
            if npcid in seen:                  # last write wins, as MySQL REPLACE would
                counts["duplicate"] += 1
                out[seen[npcid]] = row
                continue
            seen[npcid] = len(out)
            out.append(row)

    if stats is not None:
        stats.update(counts)
    return out


def parse_dump_file(path: Path, stats: dict | None = None) -> list[dict]:
    """Read a ``npc_list`` mysqldump off disk (latin-1, binary-safe) and parse it."""
    return parse_dump(Path(path).read_text(encoding="latin-1"), stats)


def build(rows_in: list[dict], meta: dict | None = None) -> bytes:
    """Encode rows into the gzipped-JSON snapshot format, sorted by ``npcid``."""
    info = dict(meta or {})
    info.setdefault("table", "npc_list")
    info.setdefault("rows", len(rows_in))
    info.setdefault(
        "note",
        "Fallback snapshot for offline preview. A reachable server database always "
        "wins per-id; this only fills ids the database did not answer.")

    npcs: dict = {}
    for r in sorted(rows_in, key=lambda r: int(r["npcid"])):
        # binary(20) in MySQL, so pad/trim to match what the server would have loaded.
        look = bytes(r["look"] or b"").ljust(20, b"\0")[:20]
        npcs[str(int(r["npcid"]))] = {
            "name": _as_bytes(r["name"]).decode("utf-8", "replace"),
            "look": look.hex(),
            "pos": [round(float(r["pos_x"]), 3), round(float(r["pos_y"]), 3),
                    round(float(r["pos_z"]), 3)],
            "rot": int(r["pos_rot"]) & 0xFF,
        }
    info["rows"] = len(npcs)

    doc = {"format": FORMAT, "meta": info, "npcs": npcs}
    raw = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw, 9, mtime=0)   # mtime=0 → byte-identical rebuilds
